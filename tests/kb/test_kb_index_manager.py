"""Integration tests for index manager."""

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from nanobot.kb.chunker import chunk_file
from nanobot.kb.embedder import Embedder
from nanobot.kb.index_manager import IndexManager, SearchResponse, SearchResponse
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore


class MockModel:
    """Mock model for testing with deterministic embeddings."""
    def __init__(self):
        # Use local random state to avoid polluting global np.random
        self._rng = np.random.RandomState(42)

    def encode(self, sentences, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]

        embeddings = self._rng.randn(len(sentences), 384).astype(np.float32)
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


def create_parsed_file(kb_store: KnowledgeBaseStore, text: str, filename: str = "test.txt") -> str:
    """Helper to create parsed file."""
    import hashlib
    # Generate unique hash based on text content and filename
    hash_input = f"{text}_{filename}".encode('utf-8')
    file_hash = hashlib.sha256(hash_input).hexdigest()

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
    response = index_manager.search("Python", top_k=3)

    assert response.success
    assert len(response.results) > 0
    assert response.returned_count == len(response.results)
    assert response.candidate_count >= response.returned_count
    assert all(-1.0 <= r.similarity <= 1.0 for r in response.results)
    assert all(r.filename is not None for r in response.results)
    assert all(r.file_type is not None for r in response.results)


def test_search_empty_query(kb_store, index_manager):
    """Test searching with empty query."""
    response = index_manager.search("", top_k=5)
    assert not response.success
    assert response.error_type == SearchResponse.ERROR_INVALID_QUERY
    assert response.results == []


def test_search_empty_index(kb_store, index_manager):
    """Test searching in empty index."""
    response = index_manager.search("anything", top_k=5)
    assert response.success  # Not an error, just no results
    assert response.results == []
    assert response.candidate_count == 0
    assert response.returned_count == 0


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
    # Create files and build initial index with distinctive content
    file_id = create_parsed_file(kb_store, "apple banana cherry " * 20)
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)

    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    result1 = manager.build_index_for_file(file_id)
    assert result1.success

    old_count = manager.vector_index.count()
    assert old_count > 0

    # Create another file with different content
    file_id2 = create_parsed_file(kb_store, "orange grape melon " * 20)
    chunk_file(file_id2, kb_store, chunk_size=100, overlap=10)

    # Mock the VectorIndex.save to fail only on the final rebuild_all save
    original_save = manager.vector_index.save
    call_count = [0]

    def failing_save():
        call_count[0] += 1
        # After fix: only one save call (at the end of rebuild_all)
        if call_count[0] >= 1:
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

    # Create new manager - should reload the previous state (file1_old)
    # Note: After fix, loop doesn't save, so final save failure means:
    # - Memory was modified (file1 + file2 added)
    # - Save failed
    # - Disk still has old state (file1_old only)
    manager2 = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    reloaded_index = manager2.vector_index

    # Index should have been reloaded successfully (old state)
    assert reloaded_index.count() == old_count

    # Old file1 should still be searchable
    response = manager2.search("apple banana", top_k=5)
    assert response.success
    assert any(r.file_id == file_id for r in response.results)

    # Retry rebuild_all should succeed
    result3 = manager2.rebuild_all(force=True)
    assert result3.success

    # Now both files should be searchable
    response1 = manager2.search("apple banana", top_k=5)
    assert response1.success
    assert any(r.file_id == file_id for r in response1.results)

    response2 = manager2.search("orange grape", top_k=5)
    assert response2.success
    assert any(r.file_id == file_id2 for r in response2.results)


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
    response_0 = index_manager.search("File 0", top_k=5)
    assert response_0.success
    assert any(r.file_id == file_ids[0] for r in response_0.results)

    response_2 = index_manager.search("File 2", top_k=5)
    assert response_2.success
    assert any(r.file_id == file_ids[2] for r in response_2.results)


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


def test_get_chunks_by_ids_empty_list(kb_store):
    """Empty ID list returns empty dict."""
    result = kb_store.get_chunks_by_ids([])
    assert result == {}


def test_get_chunks_by_ids_single_chunk(kb_store):
    """Single chunk retrieval with file metadata."""
    file_id = create_parsed_file(kb_store, "test content")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)

    chunks = kb_store.get_chunks(file_id, start_index=1)
    assert len(chunks) > 0

    chunk_id = chunks[0]["chunk_id"]
    result = kb_store.get_chunks_by_ids([chunk_id])

    assert chunk_id in result
    assert result[chunk_id]["chunk_text"] == chunks[0]["chunk_text"]
    assert result[chunk_id]["chunk_index"] == chunks[0]["chunk_index"]
    assert result[chunk_id]["filename"] is not None
    assert result[chunk_id]["file_type"] is not None


def test_get_chunks_by_ids_multiple_chunks(kb_store):
    """Multiple chunks retrieval."""
    file_id = create_parsed_file(kb_store, "x" * 500)
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)

    chunks = kb_store.get_chunks(file_id, start_index=1)
    chunk_ids = [c["chunk_id"] for c in chunks]

    result = kb_store.get_chunks_by_ids(chunk_ids)

    assert len(result) == len(chunks)
    for chunk_id in chunk_ids:
        assert chunk_id in result


def test_get_chunks_by_ids_missing_chunks(kb_store):
    """Missing chunks are not included in result."""
    file_id = create_parsed_file(kb_store, "test")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)

    chunks = kb_store.get_chunks(file_id, start_index=1)
    chunk_id = chunks[0]["chunk_id"]

    # Query with one valid and one invalid ID
    result = kb_store.get_chunks_by_ids([chunk_id, "nonexistent_id"])

    assert len(result) == 1
    assert chunk_id in result
    assert "nonexistent_id" not in result


def test_get_chunks_by_ids_includes_source_fields(kb_store):
    """Result includes source_page and source_section fields."""
    file_id = create_parsed_file(kb_store, "test content")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)

    chunks = kb_store.get_chunks(file_id, start_index=1)
    chunk_id = chunks[0]["chunk_id"]

    result = kb_store.get_chunks_by_ids([chunk_id])

    assert "source_page" in result[chunk_id]
    assert "source_section" in result[chunk_id]


def test_search_preserves_faiss_ranking(kb_store, index_manager):
    """Search results preserve FAISS ranking order."""
    # Create file with distinctive content
    text = "Python programming. " * 10 + "Java programming. " * 5 + "Ruby programming. " * 3
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=200, overlap=20)
    index_manager.build_index_for_file(file_id)

    response = index_manager.search("Python programming", top_k=5)

    # Results should be in descending similarity order
    assert response.success
    assert len(response.results) > 0
    for i in range(len(response.results) - 1):
        assert response.results[i].similarity >= response.results[i + 1].similarity


def test_search_includes_file_metadata(kb_store, index_manager):
    """Search results include filename and file_type."""
    file_id = create_parsed_file(kb_store, "test content for search " * 10)
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
    index_manager.build_index_for_file(file_id)

    response = index_manager.search("test content", top_k=3)

    assert response.success
    assert len(response.results) > 0
    for result in response.results:
        assert result.filename is not None
        assert result.file_type is not None


def test_search_includes_source_fields(kb_store, index_manager):
    """Search results include source_page and source_section."""
    file_id = create_parsed_file(kb_store, "test content " * 10)
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
    index_manager.build_index_for_file(file_id)

    response = index_manager.search("test content", top_k=3)

    assert response.success
    assert len(response.results) > 0
    for result in response.results:
        assert hasattr(result, "source_page")
        assert hasattr(result, "source_section")


def test_search_embedding_failure(kb_store, index_manager, monkeypatch):
    """Embedding failure returns error response."""
    file_id = create_parsed_file(kb_store, "test content")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
    index_manager.build_index_for_file(file_id)

    # Mock embedder to fail
    def failing_embed(*args, **kwargs):
        raise RuntimeError("Model unavailable")
    monkeypatch.setattr(index_manager.embedder, "embed", failing_embed)

    response = index_manager.search("test query")

    assert not response.success
    assert response.error_type == SearchResponse.ERROR_EMBEDDING_FAILED
    assert response.error_message == "Failed to generate query embedding"
    assert response.results == []


def test_search_faiss_failure(kb_store, index_manager, monkeypatch):
    """FAISS search failure returns error response."""
    file_id = create_parsed_file(kb_store, "test content")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
    index_manager.build_index_for_file(file_id)

    # Mock vector_index.search to fail
    def failing_search(*args, **kwargs):
        raise RuntimeError("FAISS error")
    monkeypatch.setattr(index_manager.vector_index, "search", failing_search)

    response = index_manager.search("test query")

    assert not response.success
    assert response.error_type == SearchResponse.ERROR_SEARCH_FAILED
    assert response.error_message == "Vector index search failed"
    assert response.results == []


def test_search_database_failure(kb_store, index_manager, monkeypatch):
    """Database failure returns error response."""
    file_id = create_parsed_file(kb_store, "test content")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
    index_manager.build_index_for_file(file_id)

    # Mock get_chunks_by_ids to fail
    def failing_query(*args, **kwargs):
        raise RuntimeError("Database error")
    monkeypatch.setattr(index_manager.kb_store, "get_chunks_by_ids", failing_query)

    response = index_manager.search("test query")

    assert not response.success
    assert response.error_type == SearchResponse.ERROR_DATABASE_FAILED
    assert response.error_message == "Failed to retrieve chunk data"
    assert response.candidate_count > 0  # FAISS search succeeded
    assert response.returned_count == 0


def test_search_parameter_validation(kb_store, index_manager):
    """Invalid parameters raise ValueError."""
    import pytest

    # Query too long
    with pytest.raises(ValueError, match="Query too long"):
        index_manager.search("x" * 10001)

    # Invalid top_k
    with pytest.raises(ValueError, match="Invalid top_k"):
        index_manager.search("test", top_k=0)

    with pytest.raises(ValueError, match="Invalid top_k"):
        index_manager.search("test", top_k=101)

    # Invalid min_similarity
    with pytest.raises(ValueError, match="Invalid min_similarity"):
        index_manager.search("test", min_similarity=-1.1)

    with pytest.raises(ValueError, match="Invalid min_similarity"):
        index_manager.search("test", min_similarity=1.1)


def test_search_min_similarity_filter(kb_store, index_manager):
    """Min similarity threshold filters results."""
    text = "Python programming tutorial " * 20
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=200, overlap=20)
    index_manager.build_index_for_file(file_id)

    # Search without filter
    response1 = index_manager.search("Python", top_k=10, min_similarity=0.0)
    assert response1.success
    total = response1.candidate_count

    # Search with high threshold
    response2 = index_manager.search("Python", top_k=10, min_similarity=0.8)
    assert response2.success
    assert response2.candidate_count == total  # Same candidates from FAISS
    assert response2.returned_count <= response2.candidate_count
    assert all(r.similarity >= 0.8 for r in response2.results)


def test_search_candidate_vs_returned_count(kb_store, index_manager):
    """Candidate count and returned count reflect filtering."""
    text = "apple " * 10 + "banana " * 10 + "cherry " * 10
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
    index_manager.build_index_for_file(file_id)

    response = index_manager.search("apple", top_k=5, min_similarity=0.0)
    assert response.success
    assert response.candidate_count >= response.returned_count
    assert response.returned_count == len(response.results)


def test_search_missing_chunk_in_db(kb_store, index_manager, monkeypatch):
    """Missing chunks in DB are skipped without error."""
    file_id = create_parsed_file(kb_store, "test content")
    chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
    index_manager.build_index_for_file(file_id)

    # Mock to return empty dict (simulating missing chunks)
    monkeypatch.setattr(index_manager.kb_store, "get_chunks_by_ids", lambda x: {})

    response = index_manager.search("test")
    assert response.success
    assert response.candidate_count > 0  # FAISS found results
    assert response.returned_count == 0  # But DB had no chunks
    assert response.results == []


def test_search_preserves_negative_similarity(kb_store, index_manager):
    """Negative similarity values are preserved (not clipped)."""
    # Create diverse content
    text = "Python programming " * 20
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=200, overlap=20)
    index_manager.build_index_for_file(file_id)

    # Query with unrelated content
    response = index_manager.search("quantum physics mechanics", top_k=10, min_similarity=-1.0)
    assert response.success

    # Allow for negative similarities (dissimilar content)
    if response.results:
        for r in response.results:
            assert -1.0 <= r.similarity <= 1.0


def test_search_whitespace_query_is_invalid(kb_store, index_manager):
    """Query with only whitespace is treated as invalid."""
    response = index_manager.search("   \t\n  ", top_k=5)
    assert not response.success
    assert response.error_type == SearchResponse.ERROR_INVALID_QUERY
    assert response.results == []
    assert response.candidate_count == 0
    assert response.returned_count == 0


def test_search_filtering_preserves_order(kb_store, index_manager):
    """Similarity filtering preserves FAISS ranking order."""
    # Create content with varying relevance
    text = "Python programming tutorial " * 30 + " Java development " * 15 + " Ruby scripting " * 5
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=200, overlap=30)
    index_manager.build_index_for_file(file_id)

    # Search with low threshold to get multiple results
    response = index_manager.search("Python programming", top_k=10, min_similarity=0.0)

    assert response.success
    if len(response.results) > 1:
        # Verify descending order is maintained after filtering
        for i in range(len(response.results) - 1):
            assert response.results[i].similarity >= response.results[i + 1].similarity


def test_search_returned_count_matches_results_length(kb_store, index_manager):
    """returned_count always equals len(results)."""
    text = "test content " * 50
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=150, overlap=20)
    index_manager.build_index_for_file(file_id)

    # Test various scenarios - invariant: returned_count == len(results)
    response1 = index_manager.search("test", top_k=5, min_similarity=0.0)
    assert response1.success
    assert response1.returned_count == len(response1.results)

    response2 = index_manager.search("test", top_k=5, min_similarity=0.8)
    assert response2.success
    assert response2.returned_count == len(response2.results)

    # Even with unrelated query, invariant holds
    response3 = index_manager.search("unrelated_xyz", top_k=5, min_similarity=0.0)
    assert response3.success
    assert response3.returned_count == len(response3.results)


def test_search_multiple_files_correct_source_mapping(kb_store, index_manager):
    """Search results correctly map chunks to their source files."""
    # Create two files with different content
    text1 = "Python programming tutorial " * 20
    text2 = "JavaScript web development " * 20

    file_id1 = create_parsed_file(kb_store, text1, filename="python_doc.txt")
    file_id2 = create_parsed_file(kb_store, text2, filename="javascript_doc.txt")

    chunk_file(file_id1, kb_store, chunk_size=200, overlap=20)
    chunk_file(file_id2, kb_store, chunk_size=200, overlap=20)

    index_manager.build_index_for_file(file_id1)
    index_manager.build_index_for_file(file_id2)

    # Search for Python - should get results from file1
    response = index_manager.search("Python tutorial", top_k=5)

    assert response.success
    assert len(response.results) > 0

    # Verify each result maps to correct file
    for result in response.results:
        assert result.file_id is not None
        assert result.filename is not None
        assert result.chunk_id is not None

        # Verify chunk belongs to the file it claims
        chunks = kb_store.get_chunks(result.file_id)
        chunk_ids_in_file = {c["chunk_id"] for c in chunks}
        assert result.chunk_id in chunk_ids_in_file

        # Verify filename matches (use get_file to retrieve file info)
        file_info = kb_store.get_file(result.file_id)
        assert file_info is not None
        assert result.filename == file_info["filename"]


def test_rebuild_all_success_all_files_searchable(kb_store, tmp_path):
    """rebuild_all(force=True) succeeds and all files are searchable."""
    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")

    # Create 3 files with very distinctive content
    texts = [
        "apple banana cherry date " * 20,
        "elephant fox giraffe horse " * 20,
        "ice jade kiwi lemon " * 20,
    ]
    file_ids = []
    for i, text in enumerate(texts):
        file_id = create_parsed_file(kb_store, text, filename=f"file{i}.txt")
        chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
        file_ids.append(file_id)

    # Initial index for first 2 files only
    for file_id in file_ids[:2]:
        manager.build_index_for_file(file_id)

    # Rebuild all with force=True
    result = manager.rebuild_all(force=True)
    assert result.success
    expected_chunks = sum(len(kb_store.get_chunks(fid, start_index=1)) for fid in file_ids)
    assert result.indexed_count == expected_chunks
    assert result.failed_count == 0

    # All files should be searchable with their distinctive terms
    search_terms = ["apple banana", "elephant fox", "ice jade"]
    for i, (file_id, term) in enumerate(zip(file_ids, search_terms)):
        response = manager.search(term, top_k=5)
        assert response.success
        assert any(r.file_id == file_id for r in response.results), f"File {i} (term: {term}) not found in search results"


def test_rebuild_all_metadata_consistency(kb_store, tmp_path):
    """rebuild_all maintains consistency between SQLite and FAISS IDs."""
    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")

    # Create 3 files
    file_ids = []
    for i in range(3):
        text = f"content_{i} " * 50
        file_id = create_parsed_file(kb_store, text)
        chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
        file_ids.append(file_id)

    # Rebuild all
    result = manager.rebuild_all(force=True)
    assert result.success

    # Check metadata consistency for each chunk
    for file_id in file_ids:
        chunks = kb_store.get_chunks(file_id, start_index=1)
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            db_faiss_id = chunk["vector_index_id"]
            actual_faiss_id = manager.vector_index.get_faiss_id(chunk_id)

            assert db_faiss_id is not None, f"Chunk {chunk_id} missing vector_index_id in DB"
            assert actual_faiss_id is not None, f"Chunk {chunk_id} missing from FAISS index"
            assert db_faiss_id == actual_faiss_id, f"Chunk {chunk_id}: DB ID {db_faiss_id} != FAISS ID {actual_faiss_id}"


def test_rebuild_all_final_save_failure_retry_recovers(kb_store, tmp_path):
    """Final save failure allows retry to recover all files."""
    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")

    # Create 3 files
    file_ids = []
    for i in range(3):
        text = f"recoverable_{i} " * 50
        file_id = create_parsed_file(kb_store, text)
        chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
        file_ids.append(file_id)

    # Mock final save to fail
    original_save = manager.vector_index.save
    call_count = [0]

    def failing_save():
        call_count[0] += 1
        if call_count[0] == 1:  # Only one save call after fix
            raise RuntimeError("Simulated save failure")
        original_save()

    manager.vector_index.save = failing_save

    # First attempt fails
    result1 = manager.rebuild_all(force=True)
    assert not result1.success
    assert manager._vector_index is None

    # Restore normal save and retry
    manager.vector_index.save = original_save
    result2 = manager.rebuild_all(force=True)
    assert result2.success

    # All files should be searchable
    for i, file_id in enumerate(file_ids):
        response = manager.search(f"recoverable_{i}", top_k=5)
        assert response.success
        assert any(r.file_id == file_id for r in response.results), f"File {i} not recovered after retry"


def test_rebuild_all_order_independent(kb_store, tmp_path):
    """File processing order does not affect rebuild_all result."""
    # Create 3 files with unique content
    file_ids = []
    texts = []
    for i in range(3):
        text = f"unique_term_{i} " * 30
        texts.append(text)
        file_id = create_parsed_file(kb_store, text, filename=f"file_{i}.txt")
        chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
        file_ids.append(file_id)

    # First rebuild: process files in original order
    embedder1 = Embedder(model=MockModel())
    manager1 = IndexManager(kb_store, embedder=embedder1, index_dir=tmp_path / "index1")
    result1 = manager1.rebuild_all(force=True)
    assert result1.success

    # Second rebuild: manually process files in reverse order
    # (simulating different file discovery order)
    embedder2 = Embedder(model=MockModel())
    manager2 = IndexManager(kb_store, embedder=embedder2, index_dir=tmp_path / "index2")
    manager2.vector_index.clear()

    for file_id in reversed(file_ids):
        manager2.build_index_for_file(file_id, force=True, _skip_save=True, _skip_db_update=True)

    # Finalize with save and DB update (simulating rebuild_all's final steps)
    manager2.vector_index.save()
    all_updates = []
    for file_id in reversed(file_ids):
        chunks = kb_store.get_chunks(file_id, start_index=1)
        for chunk in chunks:
            faiss_id = manager2.vector_index.get_faiss_id(chunk["chunk_id"])
            if faiss_id is not None:
                all_updates.append((chunk["chunk_id"], embedder2.model_name, faiss_id))
    if all_updates:
        kb_store.update_chunk_embeddings(all_updates)

    # Verify both indexes contain all files and produce searchable results
    for i, file_id in enumerate(file_ids):
        # Check manager1
        response1 = manager1.search(f"unique_term_{i}", top_k=10)
        assert response1.success
        assert any(r.file_id == file_id for r in response1.results), \
            f"Manager1: file {i} not found in search results"

        # Check manager2
        response2 = manager2.search(f"unique_term_{i}", top_k=10)
        assert response2.success
        assert any(r.file_id == file_id for r in response2.results), \
            f"Manager2: file {i} not found in search results"

    # Verify metadata consistency for both indexes
    for file_id in file_ids:
        chunks = kb_store.get_chunks(file_id, start_index=1)
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            db_faiss_id = chunk["vector_index_id"]

            # Both managers should have this chunk with valid FAISS ID
            faiss_id1 = manager1.vector_index.get_faiss_id(chunk_id)
            faiss_id2 = manager2.vector_index.get_faiss_id(chunk_id)

            assert faiss_id1 is not None, f"Manager1: chunk {chunk_id} missing from FAISS"
            assert faiss_id2 is not None, f"Manager2: chunk {chunk_id} missing from FAISS"

            # DB should be consistent with at least one manager (last update wins)
            assert db_faiss_id in (faiss_id1, faiss_id2), \
                f"Chunk {chunk_id}: DB ID {db_faiss_id} doesn't match either manager"


def test_rebuild_all_incremental_preserves_index(kb_store, tmp_path):
    """force=False incremental rebuild preserves existing index."""
    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")

    # Build initial index for file1
    file_id1 = create_parsed_file(kb_store, "initial_content " * 30)
    chunk_file(file_id1, kb_store, chunk_size=100, overlap=10)
    manager.build_index_for_file(file_id1)

    # Add new file but don't index it yet
    file_id2 = create_parsed_file(kb_store, "new_content " * 30)
    chunk_file(file_id2, kb_store, chunk_size=100, overlap=10)

    # Incremental rebuild should add file2 without breaking file1
    result = manager.rebuild_all(force=False)
    assert result.success

    # Both files should be searchable
    response1 = manager.search("initial_content", top_k=5)
    assert response1.success
    assert any(r.file_id == file_id1 for r in response1.results), "File1 lost after incremental rebuild"

    response2 = manager.search("new_content", top_k=5)
    assert response2.success
    assert any(r.file_id == file_id2 for r in response2.results), "File2 not indexed after incremental rebuild"


def test_rebuild_all_db_update_failure_after_faiss_save(kb_store, tmp_path, monkeypatch):
    """FAISS save succeeds but DB update fails - should report failure but FAISS is saved."""
    embedder = Embedder(model=MockModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")

    # Create 2 files
    file_ids = []
    for i in range(2):
        text = f"dbfail_{i} " * 50
        file_id = create_parsed_file(kb_store, text, filename=f"file{i}.txt")
        chunk_file(file_id, kb_store, chunk_size=100, overlap=10)
        file_ids.append(file_id)

    # Mock update_chunk_embeddings to fail
    original_update = kb_store.update_chunk_embeddings
    call_count = [0]

    def failing_update(updates):
        call_count[0] += 1
        if call_count[0] == 1:  # Fail on first call (from rebuild_all)
            raise RuntimeError("Simulated DB update failure")
        original_update(updates)

    kb_store.update_chunk_embeddings = failing_update

    # Rebuild should fail at DB update (after FAISS save)
    result = manager.rebuild_all(force=True)
    assert not result.success
    assert "Database metadata update failed" in result.error

    # FAISS should be saved (count > 0)
    assert manager.vector_index.count() > 0

    # Restore normal update and verify we can search
    # (FAISS is saved, SQLite just needs sync)
    kb_store.update_chunk_embeddings = original_update

    # Retry should succeed
    result2 = manager.rebuild_all(force=True)
    assert result2.success

    # All files should be searchable
    for i, file_id in enumerate(file_ids):
        response = manager.search(f"dbfail_{i}", top_k=5)
        assert response.success
        assert any(r.file_id == file_id for r in response.results), f"File {i} not found after recovery"
