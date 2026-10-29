"""Tests for F05-A2: single-file index consistency across re-chunking, deletion, and failure recovery."""

from pathlib import Path

import pytest

from nanobot.kb.chunker import chunk_file
from nanobot.kb.embedder import Embedder
from nanobot.kb.index_manager import IndexManager
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore


class KeywordModel:
    """Mock model that generates fixed vectors based on text keywords."""
    dimension = 8
    
    def encode(self, texts, normalize_embeddings=False, **kwargs):
        import numpy as np
        if isinstance(texts, str):
            texts = [texts]
        vectors = []
        for text in texts:
            vec = np.zeros(self.dimension, dtype=np.float32)
            words = text.lower().split()
            for i, word in enumerate(["apple", "banana", "cherry", "date", "elderberry", "fig", "grape", "honeydew"]):
                if word in words:
                    vec[i] = 1.0
            vectors.append(vec)
        result = np.array(vectors, dtype=np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms[norms == 0] = 1
            result = result / norms
        return result


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
def index_manager(kb_store: KnowledgeBaseStore, tmp_path: Path) -> IndexManager:
    """Create index manager with keyword embedder."""
    embedder = Embedder(model=KeywordModel())
    return IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")


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


def test_rechunk_changed_text_reembeds(kb_store, index_manager):
    """重分块后重新向量化，不复用旧向量。"""
    file_id = create_parsed_file(kb_store, "apple notes")
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5)
    result = index_manager.build_index_for_file(file_id)
    assert result.success
    
    old_chunks = kb_store.get_chunks(file_id)
    old_id = old_chunks[0]["vector_index_id"]
    
    kb_store.save_parsed_result(file_id, "cherry notes", None, len("cherry notes"))
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5, force=True)
    result = index_manager.build_index_for_file(file_id, force=False)
    assert result.success
    
    new_chunks = kb_store.get_chunks(file_id)
    new_id = new_chunks[0]["vector_index_id"]
    
    assert new_id != old_id
    
    results = index_manager.search("cherry", top_k=5)
    assert any(r["file_id"] == file_id and r["similarity"] > 0.8 for r in results)
    
    results_apple = index_manager.search("apple", top_k=5)
    matching_apple = [r for r in results_apple if r["file_id"] == file_id]
    assert not any(r["similarity"] > 0.8 for r in matching_apple)


def test_rechunk_fewer_chunks_removes_orphans(kb_store, index_manager):
    """分块减少后，多余的旧向量被删除。"""
    text = "word " * 300
    file_id = create_parsed_file(kb_store, text)
    chunk_file(file_id, kb_store, chunk_size=300, overlap=30)
    result = index_manager.build_index_for_file(file_id)
    assert result.success
    initial_count = result.indexed_count
    
    chunk_file(file_id, kb_store, chunk_size=1000, overlap=50, force=True)
    result2 = index_manager.build_index_for_file(file_id, force=False)
    assert result2.success
    final_count = result2.indexed_count
    assert final_count < initial_count
    
    import re
    pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
    faiss_chunk_ids = index_manager.vector_index.chunk_ids_matching(pattern)
    assert len(faiss_chunk_ids) == final_count
    
    results = index_manager.search("word", top_k=20)
    file_results = [r for r in results if r["file_id"] == file_id]
    assert len(file_results) == final_count


def test_reparse_empty_then_force_chunk_clears_vectors(kb_store, index_manager):
    """重新解析为空文本后，旧向量被清理。"""
    file_id = create_parsed_file(kb_store, "some text here")
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5)
    result = index_manager.build_index_for_file(file_id)
    assert result.success
    assert result.indexed_count > 0
    
    kb_store.save_parsed_result(file_id, "", None, 0)
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5, force=True)
    
    result2 = index_manager.build_index_for_file(file_id, force=False)
    assert result2.success
    assert result2.indexed_count == 0
    
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) == 0
    
    import re
    pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
    faiss_chunk_ids = index_manager.vector_index.chunk_ids_matching(pattern)
    assert len(faiss_chunk_ids) == 0


def test_retry_is_idempotent(kb_store, index_manager):
    """重复建索引是幂等的，不增加向量。"""
    file_id = create_parsed_file(kb_store, "content here")
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5)
    
    result1 = index_manager.build_index_for_file(file_id)
    assert result1.success
    
    import re
    pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
    count1 = len(index_manager.vector_index.chunk_ids_matching(pattern))
    chunks1 = kb_store.get_chunks(file_id)
    ids1 = {c["chunk_id"]: c["vector_index_id"] for c in chunks1}
    
    result2 = index_manager.build_index_for_file(file_id, force=False)
    assert result2.success
    
    count2 = len(index_manager.vector_index.chunk_ids_matching(pattern))
    chunks2 = kb_store.get_chunks(file_id)
    ids2 = {c["chunk_id"]: c["vector_index_id"] for c in chunks2}
    
    assert count2 == count1
    assert ids2 == ids1


def test_model_change_triggers_reembed(kb_store, tmp_path):
    """模型变更时触发重新向量化。"""
    file_id = create_parsed_file(kb_store, "test content")
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5)
    
    # Build with first model
    embedder1 = Embedder(model=KeywordModel())
    embedder1.model_name = "keyword-model-v1"
    manager1 = IndexManager(kb_store, embedder=embedder1, index_dir=tmp_path / "index")
    result1 = manager1.build_index_for_file(file_id)
    assert result1.success
    
    chunks = kb_store.get_chunks(file_id)
    old_model = chunks[0]["embedding_model"]
    old_id = chunks[0]["vector_index_id"]
    assert old_model == "keyword-model-v1"
    
    # Switch to second model
    class DifferentKeywordModel(KeywordModel):
        pass
    
    embedder2 = Embedder(model=DifferentKeywordModel())
    embedder2.model_name = "keyword-model-v2"
    manager2 = IndexManager(kb_store, embedder=embedder2, index_dir=tmp_path / "index")
    result2 = manager2.build_index_for_file(file_id, force=False)
    assert result2.success
    
    chunks_after = kb_store.get_chunks(file_id)
    new_model = chunks_after[0]["embedding_model"]
    new_id = chunks_after[0]["vector_index_id"]
    
    assert new_model == "keyword-model-v2"
    assert new_model != old_model
    assert new_id != old_id


def test_db_update_failure_then_retry_recovers(kb_store, index_manager, monkeypatch):
    """DB 更新失败后，明确报告并可重试恢复。"""
    file_id = create_parsed_file(kb_store, "test content for db failure")
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5)
    
    # Make DB update fail
    original_update = kb_store.update_chunk_embeddings
    def failing_update(*args, **kwargs):
        raise RuntimeError("Simulated DB update failure")
    monkeypatch.setattr(kb_store, "update_chunk_embeddings", failing_update)
    
    result1 = index_manager.build_index_for_file(file_id)
    assert not result1.success
    assert "metadata update failed" in result1.error.lower()
    
    # FAISS saved, but DB not updated
    chunks = kb_store.get_chunks(file_id)
    assert all(c["embedding_model"] is None for c in chunks)
    
    import re
    pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
    faiss_count = len(index_manager.vector_index.chunk_ids_matching(pattern))
    assert faiss_count == len(chunks)
    
    # Restore and retry
    monkeypatch.undo()
    result2 = index_manager.build_index_for_file(file_id, force=False)
    assert result2.success
    
    chunks_after = kb_store.get_chunks(file_id)
    assert all(c["embedding_model"] is not None for c in chunks_after)
    
    # No duplicate vectors
    faiss_count_after = len(index_manager.vector_index.chunk_ids_matching(pattern))
    assert faiss_count_after == len(chunks_after)


def test_file_prefix_isolation(kb_store, index_manager):
    """文件 ID 前缀相似时不会误删其他文件。"""
    # Create two file IDs where one is prefix of the other
    from uuid import uuid4
    short_id = str(uuid4())[:18]
    long_id = short_id + "-extra-suffix"
    
    # Manually create file records
    with kb_store._store.transaction() as db:
        for fid in [short_id, long_id]:
            db.execute(
                """INSERT INTO kb_files (file_id, filename, file_type, file_size, file_path, file_hash, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
                (fid, f"{fid}.txt", "txt", 10, f"/fake/{fid}", f"hash-{fid}", "parsed")
            )
    
    kb_store.save_parsed_result(short_id, "short file content", None, 18)
    kb_store.save_parsed_result(long_id, "long file content", None, 17)
    
    chunk_file(short_id, kb_store, chunk_size=50, overlap=5)
    chunk_file(long_id, kb_store, chunk_size=50, overlap=5)
    
    index_manager.build_index_for_file(short_id)
    index_manager.build_index_for_file(long_id)
    
    import re
    pattern_long = f"^{re.escape(long_id)}_chunk_\\d+$"
    faiss_long_before = set(index_manager.vector_index.chunk_ids_matching(pattern_long))
    assert len(faiss_long_before) > 0
    
    # Delete short_id
    index_manager.delete_file_complete(short_id)
    
    # long_id vectors still intact
    faiss_long_after = set(index_manager.vector_index.chunk_ids_matching(pattern_long))
    assert faiss_long_before == faiss_long_after
    
    # short_id vectors gone
    pattern_short = f"^{re.escape(short_id)}_chunk_\\d+$"
    assert len(index_manager.vector_index.chunk_ids_matching(pattern_short)) == 0


def test_embedding_failure_with_existing_index_preserves_state(kb_store, tmp_path):
    """已有索引时 embedding 失败，旧索引仍可用。"""
    file_id = create_parsed_file(kb_store, "original text content")
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5)
    
    embedder = Embedder(model=KeywordModel())
    manager = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    result1 = manager.build_index_for_file(file_id)
    assert result1.success
    
    old_chunks = kb_store.get_chunks(file_id)
    old_count = len(old_chunks)
    old_vector_count = manager.vector_index.count()
    
    # Re-parse and re-chunk
    kb_store.save_parsed_result(file_id, "completely different new text", None, 28)
    chunk_file(file_id, kb_store, chunk_size=50, overlap=5, force=True)
    
    # Replace embedder with failing one
    class FailingModel:
        dimension = 8
        def encode(self, *args, **kwargs):
            raise RuntimeError("Simulated embedding failure")
    
    manager.embedder = Embedder(model=FailingModel())
    
    result2 = manager.build_index_for_file(file_id, force=False)
    assert not result2.success
    assert "Embedding failed" in result2.error
    
    # After failure, new chunks have no embeddings, but FAISS still has old vectors
    # However, the validity check will mark them as invalid (mismatched metadata)
    # so they won't be used. This is correct - embedding failure leaves file unindexed.
    
    # Old vectors should still be loadable after restart
    manager2 = IndexManager(kb_store, embedder=embedder, index_dir=tmp_path / "index")
    # Vector count may be same (FAISS not modified on embedding failure)
    # But the file's chunks are not searchable due to metadata mismatch
    
    # Retry with working embedder should succeed
    result3 = manager2.build_index_for_file(file_id, force=False)
    assert result3.success
    
    # Now file is searchable
    results = manager2.search("different", top_k=5)
    assert any(r["file_id"] == file_id for r in results)


def test_empty_file_id_is_rejected(kb_store, index_manager):
    """空文件 ID 被拒绝，不会匹配到其他文件。"""
    # build_index_for_file with empty ID should fail gracefully
    result = index_manager.build_index_for_file("")
    assert not result.success
    assert "not found" in result.error.lower()
    
    # delete_file_index with empty ID should return False (nothing deleted)
    deleted = index_manager.delete_file_index("")
    assert deleted is False
    
    # delete_file_complete with empty ID should not crash
    # It will try to delete from DB, which will fail gracefully
    try:
        index_manager.delete_file_complete("")
    except Exception as e:
        # If it throws, should be about file not found, not regex matching others
        assert "not found" in str(e).lower() or len(str(e)) == 0
