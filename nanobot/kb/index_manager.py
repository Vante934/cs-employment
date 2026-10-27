"""Index manager for coordinating embeddings, vector index, and database."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.paths import get_runtime_subdir
from nanobot.kb.embedder import Embedder, get_embedder
from nanobot.kb.index import SearchResult, VectorIndex
from nanobot.kb.store import KnowledgeBaseStore


@dataclass
class IndexBuildResult:
    """Result of index building operation."""
    success: bool
    indexed_count: int
    failed_count: int
    error: str | None = None


class IndexManager:
    """Manages the complete indexing lifecycle."""
    
    def __init__(
        self,
        kb_store: KnowledgeBaseStore,
        embedder: Embedder | None = None,
        index_dir: Path | None = None
    ):
        """Initialize index manager.
        
        Args:
            kb_store: Knowledge base store
            embedder: Optional embedder (for testing/custom models)
            index_dir: Optional index directory (defaults to data/kb/index)
        """
        self.kb_store = kb_store
        self.embedder = embedder or get_embedder()
        
        if index_dir is None:
            index_dir = get_runtime_subdir("kb") / "index"
        
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        
        # Lazy-loaded vector index
        self._vector_index: VectorIndex | None = None
    
    @property
    def vector_index(self) -> VectorIndex:
        """Get or create vector index."""
        if self._vector_index is None:
            dimension = self.embedder.dimension
            self._vector_index = VectorIndex(dimension, self.index_dir)
            # Try to load existing index
            try:
                loaded = self._vector_index.load()
                if loaded:
                    logger.info(f"Loaded existing index with {self._vector_index.count()} vectors")
            except Exception as e:
                logger.warning(f"Failed to load existing index, will rebuild: {e}")
                self._vector_index.clear()
        
        return self._vector_index
    
    def build_index_for_file(self, file_id: str, force: bool = False) -> IndexBuildResult:
        """Build index for a single file's chunks.
        
        Args:
            file_id: File ID
            force: If True, rebuild even if already indexed
        
        Returns:
            IndexBuildResult with success status and counts
        """
        # Get file record
        file_record = self.kb_store.get_file(file_id)
        if not file_record:
            return IndexBuildResult(
                success=False,
                indexed_count=0,
                failed_count=0,
                error=f"File not found: {file_id}"
            )
        
        if file_record.get("status") != "parsed":
            return IndexBuildResult(
                success=False,
                indexed_count=0,
                failed_count=0,
                error=f"File not parsed: status={file_record.get('status')}"
            )
        
        # Get chunks (chunk_index >= 1 only, exclude full text at 0)
        chunks = self.kb_store.get_chunks(file_id, start_index=1)
        
        if not chunks:
            # No chunks to index (e.g., empty text)
            return IndexBuildResult(
                success=True,
                indexed_count=0,
                failed_count=0
            )
        
        # Check if already indexed
        if not force:
            # Check if all chunks have valid embeddings AND exist in vector index
            fully_indexed = True
            for c in chunks:
                if not c.get("embedding_model"):
                    fully_indexed = False
                    break
                # Verify vector actually exists in index
                if not self.vector_index.has_chunk(c["chunk_id"]):
                    fully_indexed = False
                    break
            
            if fully_indexed:
                return IndexBuildResult(
                    success=True,
                    indexed_count=len(chunks),
                    failed_count=0
                )
        
        # Clear old embeddings if force rebuild
        if force:
            # Get old chunks before clearing (they might have different chunk_ids)
            old_chunks = self.kb_store.get_chunks(file_id, start_index=1)
            self._remove_file_from_index(file_id, old_chunks)
            self.kb_store.clear_chunk_embeddings(file_id)
        
        # Force rebuild removes the file's current vectors and metadata first.
        if force:
            old_chunks = self.kb_store.get_chunks(file_id, start_index=1)
            self._remove_file_from_index(file_id, old_chunks)
            self.kb_store.clear_chunk_embeddings(file_id)
        
        # Reuse vectors that are already present in memory and only embed missing
        # chunks. This keeps ordinary retries idempotent after partial failures.
        chunks_to_embed = [
            chunk for chunk in chunks
            if not self.vector_index.has_chunk(chunk["chunk_id"])
        ]
        vectors: list[np.ndarray] = []
        if chunks_to_embed:
            try:
                vectors = self.embedder.embed_batch(
                    [chunk["chunk_text"] for chunk in chunks_to_embed],
                    normalize=True
                )
            except Exception as e:
                return IndexBuildResult(
                    success=False,
                    indexed_count=0,
                    failed_count=len(chunks_to_embed),
                    error=f"Embedding failed: {e}"
                )
        
        newly_added: list[str] = []
        for chunk, vector in zip(chunks_to_embed, vectors):
            try:
                self.vector_index.add(chunk["chunk_id"], vector)
                newly_added.append(chunk["chunk_id"])
            except Exception as e:
                logger.error(f"Failed to index chunk {chunk['chunk_id']}: {e}")
        
        # Persist all newly added vectors before making them authoritative in DB.
        if newly_added:
            try:
                self.vector_index.save()
            except Exception as e:
                logger.error(f"Failed to save index: {e}")
                for chunk_id in newly_added:
                    self.vector_index.remove(chunk_id)
                return IndexBuildResult(
                    success=False,
                    indexed_count=0,
                    failed_count=len(chunks),
                    error=f"Index save failed: {e}"
                )
        
        # Only persisted vectors, including reused vectors, receive success metadata.
        indexed_count = 0
        db_failures = 0
        for chunk in chunks:
            faiss_id = self.vector_index.get_faiss_id(chunk["chunk_id"])
            if faiss_id is None:
                continue
            try:
                self.kb_store.update_chunk_embedding(
                    chunk["chunk_id"],
                    self.embedder.model_name,
                    faiss_id
                )
                indexed_count += 1
            except Exception as e:
                db_failures += 1
                logger.error(f"Failed to update DB for chunk {chunk['chunk_id']}: {e}")
        
        failed_count = len(chunks) - indexed_count
        if db_failures:
            return IndexBuildResult(
                success=False,
                indexed_count=indexed_count,
                failed_count=failed_count,
                error=f"Database metadata update failed for {db_failures} chunks"
            )
        
        return IndexBuildResult(
            success=indexed_count == len(chunks),
            indexed_count=indexed_count,
            failed_count=failed_count,
            error=None if indexed_count == len(chunks) else "Some chunks were not indexed"
        )
    
    def _remove_file_from_index(self, file_id: str, chunks: list[dict[str, Any]]) -> None:
        """Remove all chunks of a file from the vector index."""
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            if self.vector_index.has_chunk(chunk_id):
                self.vector_index.remove(chunk_id)
    
    def rebuild_all(self, force: bool = False) -> IndexBuildResult:
        """Rebuild index for all parsed files.
        
        Args:
            force: If True, rebuild even if already indexed
        
        Returns:
            IndexBuildResult with total counts
        """
        if force:
            # Clear entire index
            self.vector_index.clear()
        
        # Get all parsed files
        files = self.kb_store.list_files(status="parsed")
        
        total_indexed = 0
        total_failed = 0
        
        for file_record in files:
            result = self.build_index_for_file(file_record["file_id"], force=force)
            total_indexed += result.indexed_count
            total_failed += result.failed_count
        
        # Save final index
        try:
            self.vector_index.save()
        except Exception as e:
            return IndexBuildResult(
                success=False,
                indexed_count=total_indexed,
                failed_count=total_failed,
                error=f"Index save failed: {e}"
            )
        
        return IndexBuildResult(
            success=True,
            indexed_count=total_indexed,
            failed_count=total_failed
        )
    
    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search for similar chunks.
        
        Args:
            query: Query text
            top_k: Number of results to return
        
        Returns:
            List of results with chunk info and similarity scores
        """
        if not query or not query.strip():
            return []
        
        # Check if index is empty
        if self.vector_index.count() == 0:
            return []
        
        # Embed query
        try:
            query_vector = self.embedder.embed(query, normalize=True)
        except Exception as e:
            logger.error(f"Query embedding failed: {e}")
            return []
        
        # Search vector index
        try:
            results = self.vector_index.search(query_vector, top_k=top_k)
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []
        
        # Enrich with chunk text
        enriched_results = []
        for result in results:
            # Get chunk from database
            chunks = self.kb_store.get_all_indexed_chunks()
            chunk = next((c for c in chunks if c["chunk_id"] == result.chunk_id), None)
            
            if chunk:
                enriched_results.append({
                    "chunk_id": result.chunk_id,
                    "file_id": chunk["file_id"],
                    "chunk_text": chunk["chunk_text"],
                    "chunk_index": chunk["chunk_index"],
                    "similarity": result.similarity,
                    "distance": result.distance
                })
        
        return enriched_results
    
    def delete_file_index(self, file_id: str) -> bool:
        """Delete index entries for a file.
        
        This should be called when a file is deleted or re-chunked.
        
        Args:
            file_id: File ID
        
        Returns:
            True if any vectors were removed
        """
        chunks = self.kb_store.get_chunks(file_id, start_index=1)
        if not chunks:
            return False
        
        removed_any = False
        for chunk in chunks:
            if self.vector_index.remove(chunk["chunk_id"]):
                removed_any = True
        
        if removed_any:
            self.vector_index.save()
        
        return removed_any
    
    def delete_file_complete(self, file_id: str) -> None:
        """Delete file and its index entries atomically.
        
        Coordinates deletion across SQLite and FAISS index. If index save fails,
        raises exception to prevent database deletion.
        
        Args:
            file_id: File ID to delete
        
        Raises:
            Exception: If critical failures occur during deletion
        """
        # 1. Get chunks before any deletion
        chunks = self.kb_store.get_chunks(file_id, start_index=1)
        if not chunks:
            # No chunks, just delete file record
            self.kb_store.delete_file(file_id)
            return
        
        # 2. Remove vectors from index (memory)
        removed_chunk_ids = []
        for chunk in chunks:
            if self.vector_index.remove(chunk["chunk_id"]):
                removed_chunk_ids.append(chunk["chunk_id"])
        
        if not removed_chunk_ids:
            # No vectors were in index, just delete file record
            self.kb_store.delete_file(file_id)
            return
        
        # 3. Save index (critical step). A prior failed save may have left
        # the desired deletion only in memory, so retry that pending state too.
        if removed_chunk_ids or self.vector_index.is_dirty():
            try:
                self.vector_index.save()
            except Exception as e:
                logger.error(f"Failed to save index after removing vectors: {e}")
                raise RuntimeError(
                    f"Index save failed during file deletion. "
                    f"File {file_id} not deleted. "
                    f"Retry delete_file_complete() or run rebuild_all(force=True)."
                ) from e
        
        # 4. Delete from database (after successful index save)
        try:
            self.kb_store.delete_file(file_id)
        except Exception as e:
            logger.error(f"Failed to delete file record {file_id}: {e}")
            # Index is already saved without these vectors
            # This is inconsistent but safe: search won't return these chunks
            # User can manually delete the file record
            raise RuntimeError(
                f"Database deletion failed for file {file_id}. "
                f"Vectors removed from index. "
                f"Manual recovery: retry delete_file_complete() or delete_file()"
            ) from e
