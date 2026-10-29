"""Index manager for coordinating embeddings, vector index, and database."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from nanobot.config.paths import get_runtime_subdir
from nanobot.kb.embedder import Embedder, get_embedder
from nanobot.kb.index import VectorIndex
from nanobot.kb.store import KnowledgeBaseStore


@dataclass
class IndexBuildResult:
    """Result of index building operation."""
    success: bool
    indexed_count: int
    failed_count: int
    error: str | None = None


@dataclass
class SearchResult:
    """Single search result from knowledge base."""
    # Chunk information
    chunk_id: str
    chunk_text: str
    chunk_index: int
    similarity: float  # Cosine similarity in [-1.0, 1.0], higher is more similar

    # File metadata
    file_id: str
    filename: str
    file_type: str

    # Optional source location
    source_page: int | None = None
    source_section: str | None = None


@dataclass
class SearchResponse:
    """Response from knowledge base search operation."""
    success: bool
    results: list[SearchResult]

    # Search metadata
    candidate_count: int    # FAISS results before filtering
    returned_count: int     # Final results after filtering

    # Error information (only if success=False)
    error_type: str | None = None
    error_message: str | None = None

    # Error type constants
    ERROR_INVALID_QUERY = "invalid_query"
    ERROR_EMBEDDING_FAILED = "embedding_failed"
    ERROR_SEARCH_FAILED = "search_failed"
    ERROR_DATABASE_FAILED = "database_failed"


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
            index = VectorIndex(dimension, self.index_dir)
            try:
                loaded = index.load()
            except Exception as e:
                # Never fall back to an empty index here: the next save() would
                # overwrite the on-disk index that may still be recoverable.
                logger.error(f"Failed to load vector index from {self.index_dir}: {e}")
                raise RuntimeError(
                    f"Vector index at {self.index_dir} could not be loaded: {e}. "
                    "Files were left untouched; repair or move them aside, then "
                    "run rebuild_all() to regenerate the index."
                ) from e
            if loaded:
                logger.info(f"Loaded existing index with {index.count()} vectors")
            self._vector_index = index

        return self._vector_index

    def build_index_for_file(self, file_id: str, force: bool = False, _skip_save: bool = False, _skip_db_update: bool = False) -> IndexBuildResult:
        """Build index for a single file's chunks.

        Args:
            file_id: File ID
            force: If True, rebuild even if already indexed
            _skip_save: Internal parameter to skip save for batch operations
            _skip_db_update: Internal parameter to skip DB update for batch operations

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

        # Find all chunk_ids in FAISS belonging to this file
        pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
        faiss_chunk_ids = set(self.vector_index.chunk_ids_matching(pattern))
        db_chunk_ids = {c["chunk_id"] for c in chunks}

        # Determine which vectors to remove:
        # 1. In FAISS but not in DB (chunks were deleted or reduced)
        # 2. In both, but metadata shows it's not valid (wrong model, wrong ID, or force rebuild)
        to_remove = set()

        if force:
            # Force rebuild: remove all existing vectors for this file
            to_remove = faiss_chunk_ids
        else:
            # Remove orphans (in FAISS but not in DB)
            to_remove |= faiss_chunk_ids - db_chunk_ids

            # Check validity of chunks that exist in both
            for chunk in chunks:
                chunk_id = chunk["chunk_id"]
                if chunk_id not in faiss_chunk_ids:
                    continue

                # Vector is invalid if any of these conditions hold:
                # - DB has no embedding_model (never indexed or metadata cleared)
                # - DB embedding_model doesn't match current model
                # - DB vector_index_id doesn't match FAISS ID
                db_model = chunk.get("embedding_model")
                db_faiss_id = chunk.get("vector_index_id")
                actual_faiss_id = self.vector_index.get_faiss_id(chunk_id)

                if not db_model or db_model != self.embedder.model_name or db_faiss_id != actual_faiss_id:
                    to_remove.add(chunk_id)

        # Determine which chunks to embed:
        # - In DB but either not in FAISS or marked for removal
        to_embed = []
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            if chunk_id not in faiss_chunk_ids or chunk_id in to_remove:
                to_embed.append(chunk)

        # If no chunks exist, we still need to clean up orphans
        if not chunks:
            if to_remove:
                for chunk_id in to_remove:
                    self.vector_index.remove(chunk_id)
                if not _skip_save:
                    try:
                        self.vector_index.save()
                    except Exception as e:
                        logger.error(f"Failed to save index after removing orphans: {e}")
                        self._vector_index = None
                        return IndexBuildResult(
                            success=False,
                            indexed_count=0,
                            failed_count=0,
                            error=f"Index save failed: {e}"
                        )
            return IndexBuildResult(
                success=True,
                indexed_count=0,
                failed_count=0
            )

        # Embed missing chunks (this is the only failure point before modifications)
        vectors: list[np.ndarray] = []
        if to_embed:
            try:
                vectors = self.embedder.embed_batch(
                    [chunk["chunk_text"] for chunk in to_embed],
                    normalize=True
                )
            except Exception as e:
                return IndexBuildResult(
                    success=False,
                    indexed_count=0,
                    failed_count=len(to_embed),
                    error=f"Embedding failed: {e}"
                )

        # Modify memory: remove invalid/orphaned vectors
        for chunk_id in to_remove:
            self.vector_index.remove(chunk_id)

        # Add new vectors
        newly_added: list[tuple[str, int]] = []
        for chunk, vector in zip(to_embed, vectors):
            try:
                faiss_id = self.vector_index.add(chunk["chunk_id"], vector)
                newly_added.append((chunk["chunk_id"], faiss_id))
            except Exception as e:
                logger.error(f"Failed to add chunk {chunk['chunk_id']}: {e}")

        # Persist changes to FAISS
        if to_remove or newly_added:
            if not _skip_save:
                try:
                    self.vector_index.save()
                except Exception as e:
                    logger.error(f"Failed to save index: {e}")
                    # Invalidate the cached index; next access will reload from disk
                    self._vector_index = None
                    return IndexBuildResult(
                        success=False,
                        indexed_count=0,
                        failed_count=len(chunks),
                        error=f"Index save failed: {e}"
                    )

        # Update database metadata in a single transaction
        if not _skip_db_update:
            updates: list[tuple[str, str, int]] = []
            for chunk in chunks:
                faiss_id = self.vector_index.get_faiss_id(chunk["chunk_id"])
                if faiss_id is not None:
                    updates.append((chunk["chunk_id"], self.embedder.model_name, faiss_id))

            if updates:
                try:
                    self.kb_store.update_chunk_embeddings(updates)
                except Exception as e:
                    logger.error(f"Failed to update DB metadata: {e}")
                    # Invalidate the cached index; next access will reload from disk
                    self._vector_index = None
                    return IndexBuildResult(
                        success=False,
                        indexed_count=0,
                        failed_count=len(chunks),
                        error=f"Database metadata update failed: {e}"
                    )

            indexed_count = len(updates)
        else:
            # When skipping DB update, count chunks that have FAISS IDs in memory
            indexed_count = len([c for c in chunks if self.vector_index.get_faiss_id(c["chunk_id"]) is not None])

        failed_count = len(chunks) - indexed_count

        return IndexBuildResult(
            success=indexed_count == len(chunks),
            indexed_count=indexed_count,
            failed_count=failed_count,
            error=None if indexed_count == len(chunks) else "Some chunks were not indexed"
        )
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
            result = self.build_index_for_file(
                file_record["file_id"],
                force=force,
                _skip_save=True,
                _skip_db_update=True
            )
            total_indexed += result.indexed_count
            total_failed += result.failed_count

        # Save final index (single commit point for batch operation)
        try:
            self.vector_index.save()
        except Exception as e:
            logger.error(f"Failed to save index after rebuild_all: {e}")
            # Invalidate the cached index; next access will reload from disk
            self._vector_index = None
            return IndexBuildResult(
                success=False,
                indexed_count=total_indexed,
                failed_count=total_failed,
                error=f"Index save failed: {e}"
            )

        # Batch update database metadata after FAISS is persisted
        all_updates: list[tuple[str, str, int]] = []
        for file_record in files:
            chunks = self.kb_store.get_chunks(file_record["file_id"], start_index=1)
            for chunk in chunks:
                faiss_id = self.vector_index.get_faiss_id(chunk["chunk_id"])
                if faiss_id is not None:
                    all_updates.append((chunk["chunk_id"], self.embedder.model_name, faiss_id))

        if all_updates:
            try:
                self.kb_store.update_chunk_embeddings(all_updates)
            except Exception as e:
                logger.error(f"Failed to update DB metadata after rebuild_all: {e}")
                # FAISS is already saved; DB update failure means metadata is stale
                # but will be corrected on next rebuild or individual file index
                return IndexBuildResult(
                    success=False,
                    indexed_count=total_indexed,
                    failed_count=total_failed,
                    error=f"Database metadata update failed: {e}"
                )

        # Success only if all files succeeded AND save succeeded
        return IndexBuildResult(
            success=(total_failed == 0),
            indexed_count=total_indexed,
            failed_count=total_failed
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0
    ) -> SearchResponse:
        """Search knowledge base for similar chunks.

        Args:
            query: Query text (max 10000 chars)
            top_k: Maximum number of results to return (1-100, default 5)
            min_similarity: Minimum similarity threshold in [-1.0, 1.0] (default 0.0)

        Returns:
            SearchResponse with results, metadata, and status

        Raises:
            ValueError: If parameters are out of valid range

        Examples:
            # Normal search
            response = manager.search("Python tutorial", top_k=10)
            if response.success:
                for result in response.results:
                    print(f"{result.filename}: {result.similarity:.2f}")

            # High-relevance only
            response = manager.search("API docs", min_similarity=0.7)

            # Error handling
            response = manager.search("query")
            if not response.success:
                print(f"Error: {response.error_message} (type: {response.error_type})")
        """
        # Parameter validation
        if not query or not query.strip():
            return SearchResponse(
                success=False,
                results=[],
                candidate_count=0,
                returned_count=0,
                error_type=SearchResponse.ERROR_INVALID_QUERY,
                error_message="Empty query"
            )

        if len(query) > 10000:
            raise ValueError(f"Query too long: {len(query)} chars (max 10000)")

        if not 1 <= top_k <= 100:
            raise ValueError(f"Invalid top_k: {top_k} (must be 1-100)")

        if not -1.0 <= min_similarity <= 1.0:
            raise ValueError(f"Invalid min_similarity: {min_similarity} (must be -1.0 to 1.0)")

        # Check if index is empty
        if self.vector_index.count() == 0:
            return SearchResponse(
                success=True,
                results=[],
                candidate_count=0,
                returned_count=0
            )

        # Embed query
        try:
            query_vector = self.embedder.embed(query, normalize=True)
        except Exception as e:
            logger.error(f"Query embedding failed: {e}")
            return SearchResponse(
                success=False,
                results=[],
                candidate_count=0,
                returned_count=0,
                error_type=SearchResponse.ERROR_EMBEDDING_FAILED,
                error_message="Failed to generate query embedding"
            )

        # Search vector index
        try:
            vector_results = self.vector_index.search(query_vector, top_k=top_k)
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return SearchResponse(
                success=False,
                results=[],
                candidate_count=0,
                returned_count=0,
                error_type=SearchResponse.ERROR_SEARCH_FAILED,
                error_message="Vector index search failed"
            )

        candidate_count = len(vector_results)

        # Apply similarity filter
        filtered_results = [
            r for r in vector_results
            if r.similarity >= min_similarity
        ]

        # Batch fetch chunks by IDs
        try:
            chunk_ids = [r.chunk_id for r in filtered_results]
            chunks_by_id = self.kb_store.get_chunks_by_ids(chunk_ids)
        except Exception as e:
            logger.error(f"Database query failed: {e}")
            return SearchResponse(
                success=False,
                results=[],
                candidate_count=candidate_count,
                returned_count=0,
                error_type=SearchResponse.ERROR_DATABASE_FAILED,
                error_message="Failed to retrieve chunk data"
            )

        # Build SearchResult objects, preserving FAISS ranking order
        results = []
        for vector_result in filtered_results:
            chunk = chunks_by_id.get(vector_result.chunk_id)
            if chunk:
                results.append(SearchResult(
                    chunk_id=vector_result.chunk_id,
                    chunk_text=chunk["chunk_text"],
                    chunk_index=chunk["chunk_index"],
                    similarity=vector_result.similarity,
                    file_id=chunk["file_id"],
                    filename=chunk["filename"],
                    file_type=chunk["file_type"],
                    source_page=chunk.get("source_page"),
                    source_section=chunk.get("source_section")
                ))

        return SearchResponse(
            success=True,
            results=results,
            candidate_count=candidate_count,
            returned_count=len(results)
        )

    def delete_file_index(self, file_id: str) -> bool:
        """Delete index entries for a file.

        This should be called when a file is deleted or re-chunked.

        Args:
            file_id: File ID

        Returns:
            True if any vectors were removed
        """
        pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
        chunk_ids = self.vector_index.chunk_ids_matching(pattern)

        if not chunk_ids:
            return False

        removed_any = False
        for chunk_id in chunk_ids:
            if self.vector_index.remove(chunk_id):
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
        # 1. Find all chunk_ids in FAISS belonging to this file
        pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
        chunk_ids = self.vector_index.chunk_ids_matching(pattern)

        if not chunk_ids:
            # No vectors in index, just delete file record
            self.kb_store.delete_file(file_id)
            return

        # 2. Remove vectors from index (memory)
        removed_chunk_ids = []
        for chunk_id in chunk_ids:
            if self.vector_index.remove(chunk_id):
                removed_chunk_ids.append(chunk_id)

        if not removed_chunk_ids:
            # No vectors were actually in index, just delete file record
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
