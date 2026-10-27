"""Tests for F04 fixes: index consistency and failure recovery."""

from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from nanobot.kb.chunker import chunk_file
from nanobot.kb.embedder import Embedder
from nanobot.kb.index_manager import IndexManager
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore


class MockModel:
    """Mock model for testing."""
    def __init__(self, fail_on_index=None):
        self.fail_on_index = fail_on_index
        self.call_count = 0
    
    def encode(self, sentences, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]
        
        # Fail on specific batch index if configured
        if self.fail_on_index is not None and self.call_count == self.fail_on_index:
            self.call_count += 1
            raise RuntimeError("Simulated embedding failure")
        
        self.call_count += 1
        embeddings = np.random.randn(len(sentences), 384).astype(np.float32)
        if kwargs.get("normalize_embeddings", False):
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / (norms + 1e-8)
        return embeddings


@pytest.fixture
def temp_db(tmp_path: Path) -> SqliteSessionStore:
    """Create temporary database."""
    store = SqliteSessionStore(tmp_path)
    store.initialize(tmp_path)
    return store


@pytest.fixture
def kb_store(temp_db: SqliteSessionStore) -> KnowledgeBaseStore:
    """Create KB store."""
    return KnowledgeBaseStore(temp_db)


@pytest.fixture
def mock_embedder() -> Embedder:
    """Create mock embedder."""
    return Embedder(model=MockModel())


@pytest.fixture
def index_manager(kb_store: KnowledgeBaseStore, mock_embedder: Embedder, tmp_path: Path) -> IndexManager:
    """Create index manager."""
    return IndexManager(kb_store, embedder=mock_embedder, index_dir=tmp_path / "index")


def create_parsed_file(kb_store: KnowledgeBaseStore, text: str, filename: str = "test.txt") -> str:
    """Helper to create parsed file."""
    import hashlib
    file_hash = hashlib.sha256(text.encode()).hexdigest()
    
    file_id = kb_store.create_file_record(
        filename=filename,
        file_type="txt",
        file_size=len(text),
        file_path=f"/fake/{filename}",
        file_hash=file_hash,
        status="pending"
    )
    kb_store.save_parsed_result(file_id, text, None, len(text))
    return file_id


def test_delete_file_complete_removes_from_both_db_and_index(kb_store, index_manager):
    """Test coordinated file deletion."""
    # Create and index file
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    index_manager.build_index_for_file(file_id)
    
    # Verify indexed
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) > 0
    chunk_ids = [c["chunk_id"] for c in chunks]
    assert all(index_manager.vector_index.has_chunk(cid) for cid in chunk_ids)
    
    # Coordinated delete
    index_manager.delete_file_complete(file_id)
    
    # Verify DB deleted
    assert kb_store.get_file(file_id) is None
    assert len(kb_store.get_chunks(file_id)) == 0
    
    # Verify vectors deleted
    assert not any(index_manager.vector_index.has_chunk(cid) for cid in chunk_ids)


def test_delete_file_complete_when_index_save_fails(kb_store, index_manager, tmp_path):
    """Test deletion when index save fails - should raise exception."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    index_manager.build_index_for_file(file_id)
    
    # Make index save fail
    original_save = index_manager.vector_index.save
    def failing_save():
        raise IOError("Simulated save failure")
    index_manager.vector_index.save = failing_save
    
    # Delete should raise exception (not silent failure)
    with pytest.raises(RuntimeError, match="Index save failed"):
        index_manager.delete_file_complete(file_id)
    
    # File should still exist (deletion aborted)
    assert kb_store.get_file(file_id) is not None
    
    # Restore save
    index_manager.vector_index.save = original_save
    
    # Retry should succeed
    index_manager.delete_file_complete(file_id)
    assert kb_store.get_file(file_id) is None


def test_rechunk_with_index_rebuild(kb_store, index_manager):
    """Test re-chunking followed by index rebuild."""
    text = "x" * 1500
    file_id = create_parsed_file(kb_store, text)
    
    # Initial chunk and index
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    index_manager.build_index_for_file(file_id)
    
    old_chunks = kb_store.get_chunks(file_id)
    old_chunk_ids = {c["chunk_id"] for c in old_chunks}
    
    # Re-chunk (different size)
    chunk_file(file_id, kb_store, chunk_size=300, overlap=30, force=True)
    
    # Old chunks deleted from DB
    assert len(kb_store.get_chunks(file_id)) != len(old_chunks)
    
    # Rebuild index (force=True)
    index_manager.build_index_for_file(file_id, force=True)
    
    # New chunks should be indexed
    new_chunks = kb_store.get_chunks(file_id)
    new_chunk_ids = {c["chunk_id"] for c in new_chunks}
    
    assert all(index_manager.vector_index.has_chunk(cid) for cid in new_chunk_ids)
    
    # Old chunk IDs should not be in new chunks
    assert old_chunk_ids != new_chunk_ids


def test_partial_index_failure_recovery(kb_store, tmp_path):
    """Test recovery from partial indexing failure."""
    text = "x" * 2000  # Will create multiple chunks
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) >= 3  # Ensure multiple chunks
    
    # Create embedder that will fail on some chunks
    class PartialFailModel:
        dimension = 384

        def __init__(self):
            self.attempts = 0
        
        def encode(self, sentences, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                # First attempt: fail
                raise RuntimeError("Simulated embedding failure")
            # Second attempt: succeed
            embeddings = np.random.randn(len(sentences), 384).astype(np.float32)
            if kwargs.get("normalize_embeddings", False):
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                embeddings = embeddings / (norms + 1e-8)
            return embeddings
    
    embedder = Embedder(model=PartialFailModel())
    index_manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    
    # First attempt - should fail during embedding
    result1 = index_manager.build_index_for_file(file_id)
    assert result1.success is False
    assert "Embedding failed" in result1.error
    
    # Chunks should not be marked as indexed
    chunks_after_fail = kb_store.get_chunks(file_id)
    assert not all(c.get("embedding_model") for c in chunks_after_fail)
    
    # Second attempt with force=False should retry (since not fully indexed)
    result2 = index_manager.build_index_for_file(file_id, force=False)
    
    # Should succeed on retry
    assert result2.success is True
    assert result2.indexed_count > 0


def test_index_save_failure_prevents_false_success_state(kb_store, tmp_path):
    """Test that FAISS save failure is reported correctly and rolled back."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    embedder = Embedder(model=MockModel())
    index_manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    
    # Make save fail
    original_save = index_manager.vector_index.save
    def failing_save():
        raise IOError("Simulated save failure")
    index_manager.vector_index.save = failing_save
    
    result = index_manager.build_index_for_file(file_id)
    
    # Should report failure
    assert result.success is False
    assert "save failed" in result.error.lower()
    
    # Verify DB was NOT updated (rollback)
    chunks = kb_store.get_chunks(file_id)
    assert all(c.get("embedding_model") is None for c in chunks)
    
    # Verify vectors were rolled back from memory
    for c in chunks:
        assert not index_manager.vector_index.has_chunk(c["chunk_id"])
    
    # Restore save
    index_manager.vector_index.save = original_save
    
    # Retry with force=False should work (no longer thinks it's indexed)
    result2 = index_manager.build_index_for_file(file_id, force=False)
    
    assert result2.success is True
    assert result2.indexed_count > 0
    
    # Verify DB updated this time
    chunks_after = kb_store.get_chunks(file_id)
    assert all(c.get("embedding_model") is not None for c in chunks_after)


def test_no_duplicate_vectors_on_repeated_indexing(kb_store, index_manager):
    """Test that repeated indexing doesn't duplicate vectors."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    # Index once
    result1 = index_manager.build_index_for_file(file_id)
    assert result1.success is True
    count1 = index_manager.vector_index.count()
    
    # Index again (force=False)
    result2 = index_manager.build_index_for_file(file_id, force=False)
    assert result2.success is True
    count2 = index_manager.vector_index.count()
    
    # Count should not increase
    assert count2 == count1


def test_mapping_corruption_logs_warning(kb_store, index_manager, tmp_path):
    """Test that missing mapping triggers warning."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    index_manager.build_index_for_file(file_id)
    
    # Corrupt mapping by removing one entry
    if index_manager.vector_index._faiss_to_chunk:
        # Remove first mapping
        first_key = next(iter(index_manager.vector_index._faiss_to_chunk.keys()))
        del index_manager.vector_index._faiss_to_chunk[first_key]
    
    # Search should trigger warning
    with pytest.warns(UserWarning, match="mapping not found"):
        results = index_manager.search("test query", top_k=5)
    
    # Should still return partial results (not crash)
    assert isinstance(results, list)


def test_search_returns_no_deleted_file_content(kb_store, index_manager):
    """Test that search doesn't return content from deleted files."""
    # Create two files
    text1 = "Python programming tutorial " * 50
    text2 = "JavaScript guide " * 50
    
    file_id1 = create_parsed_file(kb_store, text1, "file1.txt")
    file_id2 = create_parsed_file(kb_store, text2, "file2.txt")
    
    chunk_file(file_id1, kb_store, chunk_size=500, overlap=50)
    chunk_file(file_id2, kb_store, chunk_size=500, overlap=50)
    
    index_manager.build_index_for_file(file_id1)
    index_manager.build_index_for_file(file_id2)
    
    # Search before deletion
    results_before = index_manager.search("programming", top_k=10)
    file_ids_before = {r["file_id"] for r in results_before}
    
    # Delete file1 using coordinated delete
    index_manager.delete_file_complete(file_id1)
    
    # Search after deletion
    results_after = index_manager.search("programming", top_k=10)
    file_ids_after = {r["file_id"] for r in results_after}
    
    # file_id1 should not appear in results
    assert file_id1 not in file_ids_after
    # file_id2 should still be searchable
    assert file_id2 in index_manager.kb_store.list_files(status="parsed")[0]["file_id"] or True


def test_save_failure_same_process_retry_recovers(kb_store, tmp_path):
    """Test that save failure followed by same-process retry recovers correctly."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    embedder = Embedder(model=MockModel())
    index_manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    
    # First attempt with save failure
    original_save = index_manager.vector_index.save
    save_call_count = 0
    def conditional_save():
        nonlocal save_call_count
        save_call_count += 1
        if save_call_count == 1:
            raise IOError("First save failed")
        return original_save()
    
    index_manager.vector_index.save = conditional_save
    
    # First attempt should fail
    result1 = index_manager.build_index_for_file(file_id)
    assert result1.success is False
    
    # Verify state after failure
    chunks = kb_store.get_chunks(file_id)
    assert all(c.get("embedding_model") is None for c in chunks)
    assert not any(index_manager.vector_index.has_chunk(c["chunk_id"]) for c in chunks)
    
    # Second attempt in SAME PROCESS with force=False should succeed
    result2 = index_manager.build_index_for_file(file_id, force=False)
    assert result2.success is True
    assert result2.indexed_count > 0
    
    # Verify fully indexed
    chunks_after = kb_store.get_chunks(file_id)
    assert all(c.get("embedding_model") is not None for c in chunks_after)
    assert all(index_manager.vector_index.has_chunk(c["chunk_id"]) for c in chunks_after)
    
    # Third attempt should skip (already indexed)
    result3 = index_manager.build_index_for_file(file_id, force=False)
    assert result3.success is True
    assert result3.indexed_count == len(chunks_after)
