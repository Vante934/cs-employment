"""Integration tests for KB parsing with SQLite and filesystem."""

from pathlib import Path

import pytest

from nanobot.kb.parser import parse_file
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore


@pytest.fixture
def temp_db(tmp_path: Path) -> SqliteSessionStore:
    """Create a temporary database."""
    store = SqliteSessionStore(tmp_path)
    store.initialize(tmp_path)
    return store


@pytest.fixture
def kb_store(temp_db: SqliteSessionStore) -> KnowledgeBaseStore:
    """Create a KnowledgeBaseStore instance."""
    return KnowledgeBaseStore(temp_db)


@pytest.fixture
def kb_files_dir(tmp_path: Path) -> Path:
    """Create KB files directory."""
    kb_dir = tmp_path / "kb" / "files"
    kb_dir.mkdir(parents=True)
    return kb_dir


def create_test_file(kb_store: KnowledgeBaseStore, kb_files_dir: Path, filename: str, content: bytes) -> tuple[str, Path]:
    """Helper to create file record and actual file."""
    import hashlib
    
    file_hash = hashlib.sha256(content).hexdigest()
    file_path = kb_files_dir / f"{file_hash}{Path(filename).suffix}"
    file_path.write_bytes(content)
    
    file_id = kb_store.create_file_record(
        filename=filename,
        file_type=Path(filename).suffix[1:],
        file_size=len(content),
        file_path=str(file_path),
        file_hash=file_hash,
        status="pending"
    )
    
    return file_id, file_path


def test_parse_and_persist_success(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test complete parsing flow with persistence."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    content = "Test content for persistence".encode("utf-8")
    file_id, _ = create_test_file(kb_store, kb_files_dir, "test.txt", content)
    
    # Parse file
    result = parse_file(file_id, kb_store)
    assert result.success is True
    
    # Persist result
    kb_store.save_parsed_result(
        file_id,
        result.text,
        result.page_count,
        result.word_count
    )
    
    # Verify status updated
    record = kb_store.get_file(file_id)
    assert record["status"] == "parsed"
    assert record["word_count"] == len("Test content for persistence")
    assert record["error_message"] is None
    
    # Verify full text stored
    stored_text = kb_store.get_parsed_text(file_id)
    assert stored_text == "Test content for persistence"


def test_parse_failure_persists_error(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that parse failures are properly recorded."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    # Create file that doesn't exist
    file_id = kb_store.create_file_record(
        filename="missing.txt",
        file_type="txt",
        file_size=100,
        file_path=str(kb_files_dir / "nonexistent.txt"),
        file_hash="fakehash",
        status="pending"
    )
    
    result = parse_file(file_id, kb_store)
    
    assert result.success is False
    assert result.error is not None
    
    # Update status with error
    kb_store.update_file_status(file_id, "parse_failed", error_message=result.error)
    
    # Verify error persisted
    record = kb_store.get_file(file_id)
    assert record["status"] == "parse_failed"
    assert record["error_message"] is not None


def test_empty_text_persists_correctly(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that empty files are handled correctly."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    file_id, _ = create_test_file(kb_store, kb_files_dir, "empty.txt", b"")
    
    result = parse_file(file_id, kb_store)
    assert result.success is True
    assert result.text == ""
    assert result.word_count == 0
    
    kb_store.save_parsed_result(file_id, result.text, None, result.word_count)
    
    record = kb_store.get_file(file_id)
    assert record["status"] == "parsed"
    assert record["word_count"] == 0


def test_duplicate_parse_skipped(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that duplicate parsing is skipped."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    content = "Test content".encode("utf-8")
    file_id, _ = create_test_file(kb_store, kb_files_dir, "test.txt", content)
    
    # First parse
    result1 = parse_file(file_id, kb_store)
    kb_store.save_parsed_result(file_id, result1.text, None, result1.word_count)
    
    # Second parse without force
    result2 = parse_file(file_id, kb_store, force=False)
    assert result2.success is True
    assert result2.text is None
    assert result2.metadata.get("skipped") is True


def test_force_reparse_replaces_text(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that forced re-parsing replaces old text."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    content1 = "Original content".encode("utf-8")
    file_id, file_path = create_test_file(kb_store, kb_files_dir, "test.txt", content1)
    
    # First parse
    result1 = parse_file(file_id, kb_store)
    kb_store.save_parsed_result(file_id, result1.text, None, result1.word_count)
    
    # Modify file
    file_path.write_text("Updated content", encoding="utf-8")
    
    # Force re-parse
    result2 = parse_file(file_id, kb_store, force=True)
    assert result2.success is True
    kb_store.save_parsed_result(file_id, result2.text, None, result2.word_count)
    
    # Verify new text
    stored_text = kb_store.get_parsed_text(file_id)
    assert stored_text == "Updated content"
    
    # Verify only one chunk exists
    record = kb_store.get_file(file_id)
    assert record["word_count"] == len("Updated content")


def test_force_reparse_failure_preserves_old_text(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that failed re-parsing preserves old text."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    content = "Original content".encode("utf-8")
    file_id, file_path = create_test_file(kb_store, kb_files_dir, "test.txt", content)
    
    # First parse succeeds
    result1 = parse_file(file_id, kb_store)
    kb_store.save_parsed_result(file_id, result1.text, None, result1.word_count)
    
    # Delete file to cause parse failure
    file_path.unlink()
    
    # Force re-parse fails
    result2 = parse_file(file_id, kb_store, force=True)
    assert result2.success is False
    
    # Update status to reflect failure (don't call save_parsed_result)
    kb_store.update_file_status(file_id, "parse_failed", error_message=result2.error)
    
    # Verify old text still exists
    stored_text = kb_store.get_parsed_text(file_id)
    assert stored_text == "Original content"
    
    # Verify status reflects failure
    record = kb_store.get_file(file_id)
    assert record["status"] == "parse_failed"


def test_save_parsed_result_is_atomic(kb_store: KnowledgeBaseStore):
    """Test that save_parsed_result is atomic (all or nothing)."""
    # Create a file record
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=100,
        file_path="/fake/path.txt",
        file_hash="testhash",
        status="pending"
    )
    
    # Save parsed result
    kb_store.save_parsed_result(file_id, "Test text", None, 9)
    
    # Verify both status and chunk were saved
    record = kb_store.get_file(file_id)
    assert record["status"] == "parsed"
    assert record["word_count"] == 9
    
    stored_text = kb_store.get_parsed_text(file_id)
    assert stored_text == "Test text"


def test_delete_file_cascades_to_chunks(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that deleting a file removes associated chunks."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    content = "Test content".encode("utf-8")
    file_id, _ = create_test_file(kb_store, kb_files_dir, "test.txt", content)
    
    # Parse and persist
    result = parse_file(file_id, kb_store)
    kb_store.save_parsed_result(file_id, result.text, None, result.word_count)
    
    # Verify chunk exists
    assert kb_store.get_parsed_text(file_id) is not None
    
    # Delete file
    kb_store.delete_file(file_id)
    
    # Verify file deleted
    assert kb_store.get_file(file_id) is None
    
    # Verify chunk also deleted (CASCADE)
    assert kb_store.get_parsed_text(file_id) is None


def test_chunk_index_zero_reserved_for_full_text(kb_store: KnowledgeBaseStore):
    """Test that chunk_index=0 is used for full text."""
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=100,
        file_path="/fake/path.txt",
        file_hash="testhash",
        status="pending"
    )
    
    kb_store.save_parsed_result(file_id, "Full text content", None, 17)
    
    # Verify chunk_id format
    chunk_id = f"{file_id}_full"
    
    # Query directly to verify chunk_index
    def _check():
        with kb_store._store.transaction(write=False) as db:
            cursor = db.execute(
                "SELECT chunk_index, chunk_text FROM kb_chunks WHERE chunk_id = ?",
                (chunk_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    chunk = kb_store._store.run_read(_check)
    assert chunk is not None
    assert chunk["chunk_index"] == 0
    assert chunk["chunk_text"] == "Full text content"


def test_parse_error_not_saved_as_text(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that parse errors are not saved as full text."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    # Create file that will fail to parse
    file_id = kb_store.create_file_record(
        filename="missing.txt",
        file_type="txt",
        file_size=100,
        file_path=str(kb_files_dir / "nonexistent.txt"),
        file_hash="fakehash",
        status="pending"
    )
    
    result = parse_file(file_id, kb_store)
    
    assert result.success is False
    assert result.error is not None
    
    # Update status to reflect failure (should NOT save error as text)
    kb_store.update_file_status(file_id, "parse_failed", error_message=result.error)
    
    # Verify no chunk was created
    stored_text = kb_store.get_parsed_text(file_id)
    assert stored_text is None
    
    # Verify status and error_message are set correctly
    record = kb_store.get_file(file_id)
    assert record["status"] == "parse_failed"
    assert record["error_message"] is not None
    # Error message contains indication of failure
    assert "does not exist" in record["error_message"].lower() or "not found" in record["error_message"].lower()


def test_parse_corrupted_file_integration(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test end-to-end flow with corrupted PDF."""
    pytest.importorskip("pypdf")
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    # Create corrupted PDF
    content = b"%PDF-1.4\nCorrupted content"
    import hashlib
    file_hash = hashlib.sha256(content).hexdigest()
    file_path = kb_files_dir / f"{file_hash}.pdf"
    file_path.write_bytes(content)
    
    file_id = kb_store.create_file_record(
        filename="corrupt.pdf",
        file_type="pdf",
        file_size=len(content),
        file_path=str(file_path),
        file_hash=file_hash,
        status="pending"
    )
    
    # Parse fails
    result = parse_file(file_id, kb_store)
    assert result.success is False
    
    # Should NOT call save_parsed_result for failed parse
    # Update status only
    kb_store.update_file_status(file_id, "parse_failed", error_message=result.error)
    
    # Verify no text saved
    assert kb_store.get_parsed_text(file_id) is None
    
    # Verify error recorded
    record = kb_store.get_file(file_id)
    assert record["status"] == "parse_failed"
    assert record["error_message"] is not None


def test_large_text_truncation_integration(kb_store: KnowledgeBaseStore, kb_files_dir: Path, monkeypatch):
    """Test that large text is handled and saved correctly."""
    monkeypatch.setattr("nanobot.kb.parser.get_runtime_subdir", lambda x: kb_files_dir.parent)
    
    # Create file with >200KB text
    large_text = "x" * 250_000
    content = large_text.encode("utf-8")
    
    import hashlib
    file_hash = hashlib.sha256(content).hexdigest()
    file_path = kb_files_dir / f"{file_hash}.txt"
    file_path.write_bytes(content)
    
    file_id = kb_store.create_file_record(
        filename="large.txt",
        file_type="txt",
        file_size=len(content),
        file_path=str(file_path),
        file_hash=file_hash,
        status="pending"
    )
    
    # Parse
    result = parse_file(file_id, kb_store)
    assert result.success is True
    assert result.text is not None
    
    # Save (should save truncated text)
    kb_store.save_parsed_result(file_id, result.text, None, result.word_count)
    
    # Verify saved text is truncated
    stored_text = kb_store.get_parsed_text(file_id)
    assert stored_text is not None
    assert len(stored_text) < 250_000
    
    # Verify status
    record = kb_store.get_file(file_id)
    assert record["status"] == "parsed"
    assert record["word_count"] == len(stored_text)
