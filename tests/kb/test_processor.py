"""Tests for KB file processor."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from nanobot.kb.embedder import Embedder
from nanobot.kb.index_manager import IndexManager
from nanobot.kb.processor import ProcessResult, process_file
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore


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


class MockModel:
    """Mock embedding model for testing."""
    
    @property
    def dimension(self) -> int:
        return 3
    
    @property
    def model_name(self) -> str:
        return "mock-model"
    
    def embed(self, text: str) -> list[float]:
        # Simple deterministic embedding based on text length
        length = len(text)
        return [float(length % 3), float(length % 5), float(length % 7)]
    
    def encode(self, texts, normalize_embeddings: bool = False, convert_to_numpy: bool = True, **kwargs):
        """Batch encoding for compatibility with sentence-transformers API."""
        import numpy as np
        
        # Handle both single string and list
        if isinstance(texts, str):
            texts = [texts]
        
        embeddings = [self.embed(text) for text in texts]
        
        if normalize_embeddings:
            embeddings = [
                (np.array(emb) / (np.linalg.norm(emb) + 1e-10)).tolist()
                for emb in embeddings
            ]
        
        if convert_to_numpy:
            return np.array(embeddings)
        return embeddings


@pytest.fixture
def mock_embedder() -> Embedder:
    """Create mock embedder."""
    return Embedder(model=MockModel())


@pytest.fixture
def index_manager(kb_store: KnowledgeBaseStore, mock_embedder: Embedder, tmp_path: Path) -> IndexManager:
    """Create index manager with mock embedder."""
    return IndexManager(kb_store, embedder=mock_embedder, index_dir=tmp_path / "index")


def create_test_file_in_kb(tmp_path: Path, filename: str, content: str) -> tuple[Path, Path]:
    """Helper to create test file in proper KB directory structure.
    
    Returns:
        (kb_files_dir, test_file_path)
    """
    kb_files_dir = tmp_path / "kb" / "files"
    kb_files_dir.mkdir(parents=True, exist_ok=True)
    test_file = kb_files_dir / filename
    test_file.write_text(content, encoding="utf-8")
    return kb_files_dir, test_file


def test_process_file_success(kb_store: KnowledgeBaseStore, index_manager: IndexManager, tmp_path: Path):
    """Test successful file processing through all stages."""
    # Create KB files directory
    kb_files_dir = tmp_path / "kb" / "files"
    kb_files_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a test file in KB directory
    test_file = kb_files_dir / "test.txt"
    test_content = "This is a test document with some content for chunking. " * 20  # Repeat to ensure content
    test_file.write_text(test_content, encoding="utf-8")
    
    # Upload file
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=test_file.stat().st_size,
        file_path=str(test_file),
        file_hash="test_hash_1",
        status="pending"
    )
    
    # Mock get_runtime_subdir to return our temp path
    with patch("nanobot.kb.parser.get_runtime_subdir") as mock_subdir:
        mock_subdir.return_value = tmp_path / "kb"
        
        # Process
        result = process_file(file_id, kb_store, index_manager)
    
    # Assertions
    assert result.success is True
    assert result.file_id == file_id
    assert result.final_status == "indexed"
    assert result.error is None
    assert result.parsed is True
    assert result.chunked is True
    assert result.indexed is True
    assert result.chunk_count > 0
    assert result.indexed_count > 0
    
    # Verify database state
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "indexed"
    assert file_record["indexed_at"] is not None
    
    # Verify full text saved (chunk_index=0)
    full_text = kb_store.get_parsed_text(file_id)
    assert full_text is not None
    assert "test document" in full_text
    
    # Verify chunks saved (chunk_index>=1)
    chunks = kb_store.get_chunks(file_id, start_index=1)
    assert len(chunks) > 0
    assert all(c["chunk_index"] >= 1 for c in chunks)
    
    # Verify embeddings
    for chunk in chunks:
        assert chunk["embedding_model"] is not None
        assert chunk["vector_index_id"] is not None


def test_process_file_not_found(kb_store: KnowledgeBaseStore, index_manager: IndexManager):
    """Test processing non-existent file."""
    result = process_file("non_existent_id", kb_store, index_manager)
    
    assert result.success is False
    assert result.final_status == "failed"
    assert "not found" in result.error.lower()


def test_process_file_parse_failure(kb_store: KnowledgeBaseStore, index_manager: IndexManager, tmp_path: Path):
    """Test handling of parse failure."""
    # Create a file that will fail to parse
    test_file = tmp_path / "bad.txt"
    test_file.write_text("")  # Empty file
    
    file_id = kb_store.create_file_record(
        filename="bad.txt",
        file_type="txt",
        file_size=0,
        file_path=str(test_file),
        file_hash="bad_hash",
        status="pending"
    )
    
    # Process
    result = process_file(file_id, kb_store, index_manager)
    
    # Should fail at parse stage
    assert result.success is False
    assert result.final_status == "failed"
    assert result.parsed is False
    assert result.chunked is False
    assert result.indexed is False
    
    # Verify status updated to failed
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "failed"
    assert file_record["error_message"] is not None


def test_process_file_chunk_failure(kb_store: KnowledgeBaseStore, index_manager: IndexManager, tmp_path: Path):
    """Test handling of chunk failure."""
    kb_files_dir, test_file = create_test_file_in_kb(tmp_path, "test.txt", "Valid content")
    
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=test_file.stat().st_size,
        file_path=str(test_file),
        file_hash="test_hash_2",
        status="pending"
    )
    
    # Mock chunk_file to fail
    with patch("nanobot.kb.parser.get_runtime_subdir") as mock_subdir, \
         patch("nanobot.kb.processor.chunk_file") as mock_chunk:
        mock_subdir.return_value = tmp_path / "kb"
        from nanobot.kb.chunker import ChunkResult
        mock_chunk.return_value = ChunkResult(
            success=False,
            chunk_count=0,
            error="Chunk error"
        )
        
        result = process_file(file_id, kb_store, index_manager)
    
    # Should fail at chunk stage
    assert result.success is False
    assert result.final_status == "failed"
    assert result.parsed is True  # Parse succeeded
    assert result.chunked is False
    assert result.indexed is False
    
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "failed"


def test_process_file_index_failure(kb_store: KnowledgeBaseStore, index_manager: IndexManager, tmp_path: Path):
    """Test handling of index failure."""
    kb_files_dir, test_file = create_test_file_in_kb(tmp_path, "test.txt", "Valid content for testing")
    
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=test_file.stat().st_size,
        file_path=str(test_file),
        file_hash="test_hash_3",
        status="pending"
    )
    
    # Mock build_index_for_file to fail
    with patch("nanobot.kb.parser.get_runtime_subdir") as mock_subdir, \
         patch.object(index_manager, "build_index_for_file") as mock_index:
        mock_subdir.return_value = tmp_path / "kb"
        from nanobot.kb.index_manager import IndexBuildResult
        mock_index.return_value = IndexBuildResult(
            success=False,
            indexed_count=0,
            failed_count=1,
            error="Embedding failed"
        )
        
        result = process_file(file_id, kb_store, index_manager)
    
    # Should fail at index stage
    assert result.success is False
    assert result.final_status == "failed"
    assert result.parsed is True
    assert result.chunked is True
    assert result.indexed is False
    
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "failed"


def test_process_file_force_reprocess(kb_store: KnowledgeBaseStore, index_manager: IndexManager, tmp_path: Path):
    """Test force reprocessing of already indexed file."""
    kb_files_dir, test_file = create_test_file_in_kb(tmp_path, "test.txt", "Content for reprocessing test")
    
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=test_file.stat().st_size,
        file_path=str(test_file),
        file_hash="test_hash_4",
        status="pending"
    )
    
    with patch("nanobot.kb.parser.get_runtime_subdir") as mock_subdir:
        mock_subdir.return_value = tmp_path / "kb"
        
        # First processing
        result1 = process_file(file_id, kb_store, index_manager)
        assert result1.success is True
        
        # Second processing with force=True
        result2 = process_file(file_id, kb_store, index_manager, force=True)
        assert result2.success is True
        assert result2.final_status == "indexed"


def test_process_file_already_indexed_no_force(kb_store: KnowledgeBaseStore, index_manager: IndexManager, tmp_path: Path):
    """Test processing already indexed file without force."""
    kb_files_dir, test_file = create_test_file_in_kb(tmp_path, "test.txt", "Already indexed content")
    
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=test_file.stat().st_size,
        file_path=str(test_file),
        file_hash="test_hash_5",
        status="pending"
    )
    
    with patch("nanobot.kb.parser.get_runtime_subdir") as mock_subdir:
        mock_subdir.return_value = tmp_path / "kb"
        
        # First processing
        result1 = process_file(file_id, kb_store, index_manager)
        assert result1.success is True
        chunk_count_1 = result1.chunk_count
        
        # Second processing without force (should skip already processed stages)
        result2 = process_file(file_id, kb_store, index_manager, force=False)
        assert result2.success is True
        # Chunks should not be duplicated
        chunks = kb_store.get_chunks(file_id, start_index=1)
        assert len(chunks) == chunk_count_1


def test_process_file_stages_order(kb_store: KnowledgeBaseStore, index_manager: IndexManager, tmp_path: Path):
    """Test that processing stages execute in correct order."""
    kb_files_dir, test_file = create_test_file_in_kb(tmp_path, "test.txt", "Content to verify stage order")
    
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=test_file.stat().st_size,
        file_path=str(test_file),
        file_hash="test_hash_6",
        status="pending"
    )
    
    with patch("nanobot.kb.parser.get_runtime_subdir") as mock_subdir:
        mock_subdir.return_value = tmp_path / "kb"
        result = process_file(file_id, kb_store, index_manager)
    
    assert result.success is True
    # Verify all stages completed
    assert result.parsed is True
    assert result.chunked is True
    assert result.indexed is True
    assert result.final_status == "indexed"
    
    # Verify final database state
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "indexed"
    assert file_record["indexed_at"] is not None
