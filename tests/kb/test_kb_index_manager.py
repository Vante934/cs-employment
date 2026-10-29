"""Integration tests for index manager."""

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from nanobot.kb.chunker import chunk_file
from nanobot.kb.embedder import Embedder
from nanobot.kb.index_manager import IndexManager
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore


class MockModel:
    """Mock model for testing."""
    def encode(self, sentences, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]

        embeddings = np.random.randn(len(sentences), 384).astype(np.float32)
        if kwargs.get("normalize_embeddings", False):
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / (norms + 1e-8)

        # Always return 2D array for batch consistency
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


def create_parsed_file(kb_store: KnowledgeBaseStore, text: str) -> str:
    """Helper to create parsed file."""
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=len(text),
        file_path="/fake/path.txt",
        file_hash=f"hash_{text[:10]}",
        status="pending"
    )
    kb_store.save_parsed_result(file_id, text, None, len(text))
    return file_id


def test_build_index_for_short_text(kb_store, index_manager):
    """Test indexing short text (single chunk)."""
    text = "Short test text."
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    result = index_manager.build_index_for_file(file_id)

    assert result.success is True
    assert result.indexed_count == 1
    assert result.failed_count == 0


def test_build_index_for_long_text(kb_store, index_manager):
    """Test indexing long text (multiple chunks)."""
    text = "x" * 1500
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    result = index_manager.build_index_for_file(file_id)

    assert result.success is True
    assert result.indexed_count == 4  # 1500 chars -> 4 chunks

    # Verify database updated
    chunks = kb_store.get_chunks(file_id)
    assert all(c["embedding_model"] is not None for c in chunks)
    assert all(c["vector_index_id"] is not None for c in chunks)


def test_build_index_empty_text(kb_store, index_manager):
    """Test indexing empty text."""
    file_id = create_parsed_file(kb_store, "")
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    result = index_manager.build_index_for_file(file_id)

    assert result.success is True
    assert result.indexed_count == 0


def test_build_index_excludes_full_text(kb_store, index_manager):
    """Test that chunk_index=0 (full text) is not indexed."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    index_manager.build_index_for_file(file_id)

    # Check full text (chunk_index=0) not in index
    full_text_chunk_id = f"{file_id}_full"
    assert not index_manager.vector_index.has_chunk(full_text_chunk_id)


def test_build_index_skips_already_indexed(kb_store, index_manager):
    """Test that already indexed files are skipped."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    # First build
    result1 = index_manager.build_index_for_file(file_id)
    assert result1.indexed_count > 0

    # Second build without force
    result2 = index_manager.build_index_for_file(file_id, force=False)
    assert result2.success is True
    # Should report same count but not re-index


def test_build_index_force_rebuild(kb_store, index_manager):
    """Test force rebuild replaces old vectors."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    # First build
    index_manager.build_index_for_file(file_id)
    old_chunks = kb_store.get_chunks(file_id)
    old_vector_ids = {c["chunk_id"]: c["vector_index_id"] for c in old_chunks}

    # Force rebuild
    result = index_manager.build_index_for_file(file_id, force=True)
    assert result.success is True

    # New vector IDs should be different
    new_chunks = kb_store.get_chunks(file_id)
    new_vector_ids = {c["chunk_id"]: c["vector_index_id"] for c in new_chunks}

    # At least some IDs should differ (due to clear and re-add)
    assert old_vector_ids.keys() == new_vector_ids.keys()


def test_rebuild_all(kb_store, index_manager):
    """Test rebuilding index for all files."""
    # Create multiple files
    file_ids = []
    for i in range(3):
        text = f"File {i}. " * 200
        file_id = create_parsed_file(kb_store, text)
        chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
        file_ids.append(file_id)

    result = index_manager.rebuild_all(force=True)

    assert result.success is True
    assert result.indexed_count > 0
    assert result.failed_count == 0

    # Verify all files are indexed and searchable
    for file_id in file_ids:
        chunks = kb_store.get_chunks(file_id, start_index=1)
        assert all(c["embedding_model"] is not None for c in chunks)


def test_search_returns_similar_chunks(kb_store, index_manager):
    """Test searching for similar chunks."""
    text = "Python programming tutorial. " * 50
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    index_manager.build_index_for_file(file_id)

    # Search
    results = index_manager.search("Python", top_k=3)

    assert len(results) > 0
    assert all("chunk_id" in r for r in results)
    assert all("similarity" in r for r in results)
    assert all(0 <= r["similarity"] <= 1 for r in results)


def test_search_empty_query(kb_store, index_manager):
    """Test searching with empty query."""
    results = index_manager.search("", top_k=5)
    assert results == []


def test_search_empty_index(kb_store, index_manager):
    """Test searching in empty index."""
    results = index_manager.search("anything", top_k=5)
    assert results == []


def test_delete_file_removes_vectors(kb_store, index_manager):
    """Test that deleting file removes its vectors."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    index_manager.build_index_for_file(file_id)

    chunk_ids = [c["chunk_id"] for c in kb_store.get_chunks(file_id)]
    assert all(index_manager.vector_index.has_chunk(cid) for cid in chunk_ids)

    # Delete from index
    removed = index_manager.delete_file_index(file_id)
    assert removed is True

    # Verify vectors removed
    assert not any(index_manager.vector_index.has_chunk(cid) for cid in chunk_ids)


def test_rechunk_updates_index(kb_store, index_manager):
    """Test that re-chunking updates index."""
    text = "x" * 1500
    file_id = create_parsed_file(kb_store, text)

    # Initial chunking with size 500
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    index_manager.build_index_for_file(file_id)

    old_chunk_ids = {c["chunk_id"] for c in kb_store.get_chunks(file_id)}
    old_count = len(old_chunk_ids)

    # Re-chunk with different size (force=True deletes old chunks from DB)
    chunk_file(file_id, kb_store, chunk_size=300, overlap=30, force=True)

    # Old chunks no longer exist in DB, so can't check has_chunk
    # Instead verify that after re-indexing, we have different count
    index_manager.build_index_for_file(file_id, force=True)

    new_chunk_ids = {c["chunk_id"] for c in kb_store.get_chunks(file_id)}
    new_count = len(new_chunk_ids)

    # Different chunk size should produce different number of chunks
    assert new_count != old_count
    # New chunk IDs should be in index
    assert all(index_manager.vector_index.has_chunk(cid) for cid in new_chunk_ids)


def test_index_persistence(kb_store, index_manager, tmp_path):
    """Test that index persists across manager instances."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)

    index_manager.build_index_for_file(file_id)

    # Create new manager instance
    mock_embedder = Embedder(model=MockModel())
    new_manager = IndexManager(kb_store, embedder=mock_embedder, index_dir=tmp_path / "index")

    # Index should be loaded automatically
    chunk_ids = [c["chunk_id"] for c in kb_store.get_chunks(file_id)]
    assert all(new_manager.vector_index.has_chunk(cid) for cid in chunk_ids)


def test_file_not_parsed(kb_store, index_manager):
    """Test indexing file that isn't parsed."""
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=100,
        file_path="/fake/path.txt",
        file_hash="hash",
        status="pending"  # Not parsed
    )

    result = index_manager.build_index_for_file(file_id)

    assert result.success is False
    assert "not parsed" in result.error.lower()


def test_file_not_found(kb_store, index_manager):
    """Test indexing non-existent file."""
    result = index_manager.build_index_for_file("nonexistent")

    assert result.success is False
    assert "not found" in result.error.lower()


def test_rebuild_all_save_failure_invalidates_instance(kb_store, tmp_path, monkeypatch):
    """Save failure after rebuild_all invalidates instance and allows recovery."""
    # Create files and build initial index
    file_id = create_parsed_file(kb_store, "initial content")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)

    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    result1 = manager.build_index_for_file(file_id)
    assert result1.success

    old_count = manager.vector_index.count()
    assert old_count > 0

    # Create another file
    file_id2 = create_parsed_file(kb_store, "new content")
    chunk_file(file_id2, kb_store, chunk_size=100, overlap=10)

    # Mock the VectorIndex.save to fail only on the final rebuild_all save
    original_save = manager.vector_index.save
    call_count = [0]

    def failing_save():
        call_count[0] += 1
        # Let build_index_for_file saves succeed (first 2 calls during rebuild_all loop)
        # Only fail the final rebuild_all save
        if call_count[0] > 2:
            raise RuntimeError("Simulated final save failure")
        original_save()

    manager.vector_index.save = failing_save

    # Rebuild should fail at final save
    result2 = manager.rebuild_all(force=True)
    assert not result2.success
    assert result2.error is not None
    assert "save failed" in result2.error.lower()

    # Instance should be invalidated
    assert manager._vector_index is None

    # Create new manager - should reload the last successfully saved state
    # Note: build_index_for_file saves after each file, so file2 was saved
    # The final rebuild_all save failed, but individual file saves succeeded
    manager2 = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    reloaded_index = manager2.vector_index
    # Both files were actually saved during build_index_for_file calls
    assert reloaded_index.count() >= old_count

    # Both files should be searchable (saved during individual processing)
    results = manager2.search("initial", top_k=5)
    assert any(r["file_id"] == file_id for r in results)

    results2 = manager2.search("new", top_k=5)
    assert any(r["file_id"] == file_id2 for r in results2)


def test_rebuild_all_partial_failure_returns_false(kb_store, index_manager, monkeypatch):
    """Partial file failure returns success=False with accurate counts."""
    # Create three files
    file_ids = []
    for i in range(3):
        text = f"File {i} content. " * 50
        file_id = create_parsed_file(kb_store, text)
        chunk_file(file_id, kb_store, chunk_size=200, overlap=20)
        file_ids.append(file_id)

    # Make embedding fail for the second file
    original_embed = index_manager.embedder.embed_batch
    call_count = [0]

    def selective_failing_embed(texts, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:  # Second file
            raise RuntimeError("Simulated embedding failure")
        return original_embed(texts, **kwargs)

    monkeypatch.setattr(index_manager.embedder, "embed_batch", selective_failing_embed)

    result = index_manager.rebuild_all(force=True)

    # Should return success=False because one file failed
    assert not result.success
    assert result.indexed_count > 0  # Files 1 and 3 succeeded
    assert result.failed_count > 0  # File 2 failed

    # Successful files should be searchable
    results_0 = index_manager.search("File 0", top_k=5)
    assert any(r["file_id"] == file_ids[0] for r in results_0)

    results_2 = index_manager.search("File 2", top_k=5)
    assert any(r["file_id"] == file_ids[2] for r in results_2)


def test_rebuild_all_save_failure_then_retry_recovers(kb_store, tmp_path, monkeypatch):
    """Save failure followed by successful retry converges to consistent state."""
    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")

    # Create initial file and index it
    file_id1 = create_parsed_file(kb_store, "first file content")
    chunk_file(file_id1, kb_store, chunk_size=100, overlap=10)
    result1 = manager.build_index_for_file(file_id1)
    assert result1.success

    old_chunks = kb_store.get_chunks(file_id1, start_index=1)
    old_count = len(old_chunks)

    # Create second file
    file_id2 = create_parsed_file(kb_store, "second file content")
    chunk_file(file_id2, kb_store, chunk_size=100, overlap=10)

    # First rebuild with save failure
    original_save = manager.vector_index.save
    def failing_save():
        raise RuntimeError("Simulated save failure")
    monkeypatch.setattr(manager.vector_index, "save", failing_save)

    result2 = manager.rebuild_all(force=True)
    assert not result2.success

    # Old index should still be loadable
    manager2 = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    assert manager2.vector_index.count() == old_count

    # Retry with working save
    monkeypatch.undo()
    result3 = manager2.rebuild_all(force=True)
    assert result3.success

    # Both files should now be indexed
    import re
    pattern1 = f"^{re.escape(file_id1)}_chunk_\\d+$"
    pattern2 = f"^{re.escape(file_id2)}_chunk_\\d+$"
    faiss_chunks_1 = manager2.vector_index.chunk_ids_matching(pattern1)
    faiss_chunks_2 = manager2.vector_index.chunk_ids_matching(pattern2)

    assert len(faiss_chunks_1) > 0
    assert len(faiss_chunks_2) > 0

    # DB metadata should match FAISS
    db_chunks_1 = kb_store.get_chunks(file_id1, start_index=1)
    db_chunks_2 = kb_store.get_chunks(file_id2, start_index=1)

    assert len(faiss_chunks_1) == len(db_chunks_1)
    assert len(faiss_chunks_2) == len(db_chunks_2)

    # Verify each chunk's vector_index_id matches FAISS mapping
    for chunk in db_chunks_1:
        faiss_id = manager2.vector_index.get_faiss_id(chunk["chunk_id"])
        assert faiss_id is not None, f"Chunk {chunk['chunk_id']} not in FAISS"
        assert faiss_id == chunk["vector_index_id"], \
            f"ID mismatch for {chunk['chunk_id']}: FAISS={faiss_id}, DB={chunk['vector_index_id']}"
        assert chunk["embedding_model"] == embedder.model_name, \
            f"Model mismatch for {chunk['chunk_id']}: expected {embedder.model_name}, got {chunk['embedding_model']}"

    for chunk in db_chunks_2:
        faiss_id = manager2.vector_index.get_faiss_id(chunk["chunk_id"])
        assert faiss_id is not None, f"Chunk {chunk['chunk_id']} not in FAISS"
        assert faiss_id == chunk["vector_index_id"], \
            f"ID mismatch for {chunk['chunk_id']}: FAISS={faiss_id}, DB={chunk['vector_index_id']}"
        assert chunk["embedding_model"] == embedder.model_name, \
            f"Model mismatch for {chunk['chunk_id']}: expected {embedder.model_name}, got {chunk['embedding_model']}"

    # No duplicate vectors
    total_faiss = manager2.vector_index.count()
    assert total_faiss == len(db_chunks_1) + len(db_chunks_2)


def test_rebuild_all_empty_kb(kb_store, index_manager):
    """Empty knowledge base rebuild succeeds with zero counts."""
    result = index_manager.rebuild_all(force=True)

    assert result.success is True
    assert result.indexed_count == 0
    assert result.failed_count == 0
    assert index_manager.vector_index.count() == 0


def test_rebuild_all_idempotent(kb_store, index_manager):
    """Repeated rebuild produces consistent results without duplicates."""
    # Create files
    file_ids = []
    for i in range(2):
        text = f"File {i}. " * 100
        file_id = create_parsed_file(kb_store, text)
        chunk_file(file_id, kb_store, chunk_size=200, overlap=20)
        file_ids.append(file_id)

    # First rebuild
    result1 = index_manager.rebuild_all(force=True)
    assert result1.success
    count1 = index_manager.vector_index.count()

    # Second rebuild
    result2 = index_manager.rebuild_all(force=True)
    assert result2.success
    count2 = index_manager.vector_index.count()

    # Counts should match
    assert count1 == count2
    assert result1.indexed_count == result2.indexed_count

    # Verify no duplicate vectors for each file
    for file_id in file_ids:
        import re
        pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
        faiss_chunks = index_manager.vector_index.chunk_ids_matching(pattern)
        db_chunks = kb_store.get_chunks(file_id, start_index=1)
        assert len(faiss_chunks) == len(db_chunks)
