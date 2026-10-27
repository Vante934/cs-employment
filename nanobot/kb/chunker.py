"""Knowledge base text chunking and segmentation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nanobot.kb.store import KnowledgeBaseStore


@dataclass
class ChunkResult:
    """Result of text chunking operation."""
    
    success: bool
    chunk_count: int
    error: str | None = None


def chunk_file(
    file_id: str,
    kb_store: KnowledgeBaseStore,
    chunk_size: int = 500,
    overlap: int = 50,
    force: bool = False
) -> ChunkResult:
    """Split parsed text into chunks for embedding.
    
    Chunks start at chunk_index=1. chunk_index=0 is reserved for full text (F02).
    
    Args:
        file_id: File ID in kb_files table
        kb_store: Knowledge base store instance
        chunk_size: Characters per chunk (default 500)
        overlap: Overlapping characters between chunks (default 50)
        force: Force re-chunking even if already chunked
    
    Returns:
        ChunkResult with success status and chunk count
    """
    # 1. Check if file exists and is parsed
    record = kb_store.get_file(file_id)
    if not record:
        return ChunkResult(
            success=False,
            chunk_count=0,
            error=f"File not found: {file_id}"
        )
    
    if record.get("status") != "parsed":
        return ChunkResult(
            success=False,
            chunk_count=0,
            error=f"File not parsed: status={record.get('status')}"
        )
    
    # 2. Check if already chunked
    if not force:
        existing_chunks = kb_store.count_chunks(file_id, start_index=1)
        if existing_chunks > 0:
            return ChunkResult(
                success=True,
                chunk_count=existing_chunks,
                error=None
            )
    
    # 3. Get full text
    full_text = kb_store.get_parsed_text(file_id)
    if full_text is None:
        return ChunkResult(
            success=False,
            chunk_count=0,
            error="Full text not found (chunk_index=0 missing)"
        )
    
    # 4. Split into chunks
    chunks = split_text(full_text, chunk_size=chunk_size, overlap=overlap)
    
    # 5. Save chunks to database
    try:
        kb_store.save_chunks(file_id, chunks, replace_existing=force)
        return ChunkResult(
            success=True,
            chunk_count=len(chunks),
            error=None
        )
    except Exception as e:
        return ChunkResult(
            success=False,
            chunk_count=0,
            error=f"Failed to save chunks: {e}"
        )


def split_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50
) -> list[str]:
    """Split text into overlapping chunks.
    
    Args:
        text: Input text
        chunk_size: Maximum characters per chunk
        overlap: Overlapping characters between chunks
    
    Returns:
        List of text chunks
    """
    if not text:
        return []
    
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        # Last chunk
        if end >= len(text):
            chunks.append(text[start:])
            break
        
        # Regular chunk
        chunks.append(text[start:end])
        start = end - overlap
    
    return chunks
