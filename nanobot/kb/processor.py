"""Knowledge base file processor - orchestrates parsing, chunking, and indexing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from nanobot.kb.chunker import chunk_file
from nanobot.kb.index_manager import IndexManager
from nanobot.kb.parser import parse_file
from nanobot.kb.store import KnowledgeBaseStore


@dataclass
class ProcessResult:
    """Complete file processing result."""
    
    success: bool
    file_id: str
    final_status: str  # "indexed" | "failed"
    error: str | None = None
    
    # Stage details
    parsed: bool = False
    chunked: bool = False
    indexed: bool = False
    
    chunk_count: int = 0
    indexed_count: int = 0


def process_file(
    file_id: str,
    kb_store: KnowledgeBaseStore,
    index_manager: IndexManager,
    force: bool = False
) -> ProcessResult:
    """Process a file through the complete KB pipeline.
    
    Stages:
    1. Parse: Extract text from file
    2. Save: Save full text to kb_chunks (chunk_index=0) and update status to 'parsed'
    3. Chunk: Split into chunks and save to kb_chunks (chunk_index>=1)
    4. Index: Generate embeddings and build FAISS index
    
    Args:
        file_id: File ID (must exist in kb_files)
        kb_store: Knowledge base store
        index_manager: Index manager
        force: Force reprocessing even if already processed
    
    Returns:
        ProcessResult with detailed status
    """
    start_time = datetime.now()
    result = ProcessResult(
        success=False,
        file_id=file_id,
        final_status="failed"
    )
    
    # Verify file exists
    file_record = kb_store.get_file(file_id)
    if not file_record:
        result.error = f"File not found: {file_id}"
        logger.error(f"process_file: {result.error}")
        return result
    
    filename = file_record.get("filename", "unknown")
    logger.info(f"Processing file {file_id} ({filename})")
    
    # Stage 1: Parse
    parse_start = datetime.now()
    try:
        logger.debug(f"[{file_id}] Stage 1: Parsing...")
        
        # Note: parse_file will handle status transitions internally
        parse_result = parse_file(file_id, kb_store, force=force)
        
        if not parse_result.success:
            error_msg = f"Parse failed: {parse_result.error}"
            kb_store.update_file_status(file_id, "failed", error_message=error_msg)
            result.error = parse_result.error
            logger.error(f"[{file_id}] {error_msg}")
            return result
        
        parse_elapsed = (datetime.now() - parse_start).total_seconds()
        logger.debug(f"[{file_id}] Parse completed in {parse_elapsed:.2f}s")
        result.parsed = True
        
    except Exception as e:
        logger.exception(f"[{file_id}] Parse stage exception")
        kb_store.update_file_status(file_id, "failed", error_message=f"Parse exception: {e}")
        result.error = str(e)
        return result
    
    # Stage 2: Save full text and update status to 'parsed'
    save_start = datetime.now()
    try:
        logger.debug(f"[{file_id}] Stage 2: Saving full text...")
        
        # If parse was skipped (already parsed), text will be None
        if parse_result.text is None:
            # File was already parsed, get existing text
            existing_text = kb_store.get_parsed_text(file_id)
            if existing_text is None:
                error_msg = "File marked as parsed but no text found in database"
                kb_store.update_file_status(file_id, "failed", error_message=error_msg)
                result.error = error_msg
                logger.error(f"[{file_id}] {error_msg}")
                return result
            logger.debug(f"[{file_id}] Using existing parsed text")
        elif not parse_result.text:
            error_msg = "Parse returned empty text"
            kb_store.update_file_status(file_id, "failed", error_message=error_msg)
            result.error = error_msg
            logger.error(f"[{file_id}] {error_msg}")
            return result
        else:
            # Save full text to chunk_index=0 and atomically update status to 'parsed'
            kb_store.save_parsed_result(
                file_id,
                parse_result.text,
                page_count=parse_result.page_count,
                word_count=parse_result.word_count
            )
        
        save_elapsed = (datetime.now() - save_start).total_seconds()
        logger.debug(f"[{file_id}] Full text saved in {save_elapsed:.2f}s")
        
    except Exception as e:
        logger.exception(f"[{file_id}] Save full text exception")
        kb_store.update_file_status(file_id, "failed", error_message=f"Save exception: {e}")
        result.error = str(e)
        return result
    
    # Stage 3: Chunk
    chunk_start = datetime.now()
    try:
        logger.debug(f"[{file_id}] Stage 3: Chunking...")
        
        chunk_result = chunk_file(file_id, kb_store, force=force)
        
        if not chunk_result.success:
            error_msg = f"Chunk failed: {chunk_result.error}"
            kb_store.update_file_status(file_id, "failed", error_message=error_msg)
            result.error = chunk_result.error
            logger.error(f"[{file_id}] {error_msg}")
            return result
        
        result.chunked = True
        result.chunk_count = chunk_result.chunk_count
        
        chunk_elapsed = (datetime.now() - chunk_start).total_seconds()
        logger.debug(f"[{file_id}] Chunked into {chunk_result.chunk_count} chunks in {chunk_elapsed:.2f}s")
        
    except Exception as e:
        logger.exception(f"[{file_id}] Chunk stage exception")
        kb_store.update_file_status(file_id, "failed", error_message=f"Chunk exception: {e}")
        result.error = str(e)
        return result
    
    # Stage 4: Index
    index_start = datetime.now()
    try:
        logger.debug(f"[{file_id}] Stage 4: Building index...")
        
        index_result = index_manager.build_index_for_file(file_id, force=force)
        
        if not index_result.success:
            error_msg = f"Index failed: {index_result.error}"
            kb_store.update_file_status(file_id, "failed", error_message=error_msg)
            result.error = index_result.error
            logger.error(f"[{file_id}] {error_msg}")
            return result
        
        result.indexed = True
        result.indexed_count = index_result.indexed_count
        
        index_elapsed = (datetime.now() - index_start).total_seconds()
        logger.debug(f"[{file_id}] Indexed {index_result.indexed_count} chunks in {index_elapsed:.2f}s")
        
    except Exception as e:
        logger.exception(f"[{file_id}] Index stage exception")
        kb_store.update_file_status(file_id, "failed", error_message=f"Index exception: {e}")
        result.error = str(e)
        return result
    
    # Success: update final status to 'indexed'
    try:
        indexed_at = datetime.now().isoformat()
        kb_store.update_file_status(file_id, "indexed", indexed_at=indexed_at)
        result.success = True
        result.final_status = "indexed"
        
        total_elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"[{file_id}] Processing completed successfully in {total_elapsed:.2f}s: "
            f"{result.chunk_count} chunks, {result.indexed_count} indexed"
        )
        
    except Exception as e:
        logger.exception(f"[{file_id}] Failed to update final status to 'indexed'")
        result.error = f"Status update failed: {e}"
        result.final_status = "failed"
        return result
    
    return result
