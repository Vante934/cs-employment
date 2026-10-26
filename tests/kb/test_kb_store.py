"""Test knowledge base storage layer."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

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


def test_migration_creates_kb_tables(tmp_path: Path):
    """Test that migration creates KB tables on new database."""
    store = SqliteSessionStore(tmp_path)
    store.initialize(tmp_path)
    
    # Verify tables exist
    def check_tables():
        with store.transaction(write=False) as db:
            cursor = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kb_%'"
            )
            tables = [row[0] for row in cursor.fetchall()]
            return tables
    
    tables = store.run_read(check_tables)
    assert "kb_files" in tables
    assert "kb_chunks" in tables
    assert "kb_config" in tables


def test_migration_upgrades_v1_database(tmp_path: Path):
    """Test that v1 database is upgraded to v2 with KB tables."""
    # Create v1 database manually
    db_path = tmp_path / "sessions.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE storage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    
    # Initialize should upgrade to v2
    store = SqliteSessionStore(tmp_path)
    store.initialize(tmp_path)
    
    # Verify upgrade
    def check_version_and_tables():
        with store.transaction(write=False) as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            cursor = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kb_%'"
            )
            tables = [row[0] for row in cursor.fetchall()]
            return version, tables
    
    version, tables = store.run_read(check_version_and_tables)
    assert version == 2
    assert "kb_files" in tables
    assert "kb_chunks" in tables
    assert "kb_config" in tables


def test_create_file_record(kb_store: KnowledgeBaseStore):
    """Test creating a file record."""
    file_id = kb_store.create_file_record(
        filename="test.pdf",
        file_type="pdf",
        file_size=1024,
        file_path="/path/to/test.pdf",
        file_hash="abc123",
        feishu_message_id="msg_001",
        sender_id="user_001"
    )
    
    assert file_id is not None
    assert len(file_id) == 36  # UUID format
    
    # Verify record
    file_record = kb_store.get_file(file_id)
    assert file_record is not None
    assert file_record["filename"] == "test.pdf"
    assert file_record["file_type"] == "pdf"
    assert file_record["file_size"] == 1024
    assert file_record["file_path"] == "/path/to/test.pdf"
    assert file_record["file_hash"] == "abc123"
    assert file_record["status"] == "pending"
    assert file_record["feishu_message_id"] == "msg_001"
    assert file_record["sender_id"] == "user_001"


def test_file_hash_uniqueness(kb_store: KnowledgeBaseStore):
    """Test that file_hash UNIQUE constraint works."""
    file_hash = "duplicate_hash"
    
    # First insert succeeds
    kb_store.create_file_record(
        filename="file1.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/1.pdf",
        file_hash=file_hash
    )
    
    # Second insert with same hash should fail
    with pytest.raises(sqlite3.IntegrityError):
        kb_store.create_file_record(
            filename="file2.pdf",
            file_type="pdf",
            file_size=200,
            file_path="/path/2.pdf",
            file_hash=file_hash
        )


def test_file_exists(kb_store: KnowledgeBaseStore):
    """Test file existence check."""
    file_hash = "test_hash"
    
    assert not kb_store.file_exists(file_hash)
    
    kb_store.create_file_record(
        filename="test.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/test.pdf",
        file_hash=file_hash
    )
    
    assert kb_store.file_exists(file_hash)


def test_get_file_by_hash(kb_store: KnowledgeBaseStore):
    """Test retrieving file by hash."""
    file_hash = hashlib.sha256(b"test content").hexdigest()
    
    file_id = kb_store.create_file_record(
        filename="test.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/test.pdf",
        file_hash=file_hash
    )
    
    file_record = kb_store.get_file_by_hash(file_hash)
    assert file_record is not None
    assert file_record["file_id"] == file_id
    assert file_record["file_hash"] == file_hash


def test_update_file_status(kb_store: KnowledgeBaseStore):
    """Test updating file status."""
    file_id = kb_store.create_file_record(
        filename="test.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/test.pdf",
        file_hash="hash1"
    )
    
    # Update to parsing
    kb_store.update_file_status(file_id, "parsing")
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "parsing"
    
    # Update to indexed with metadata
    kb_store.update_file_status(
        file_id,
        "indexed",
        summary="Test summary",
        keywords='["test", "pdf"]',
        page_count=5,
        word_count=1000
    )
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "indexed"
    assert file_record["summary"] == "Test summary"
    assert file_record["keywords"] == '["test", "pdf"]'
    assert file_record["page_count"] == 5
    assert file_record["word_count"] == 1000


def test_update_file_status_with_error(kb_store: KnowledgeBaseStore):
    """Test updating file status to failed with error message."""
    file_id = kb_store.create_file_record(
        filename="bad.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/bad.pdf",
        file_hash="hash_bad"
    )
    
    kb_store.update_file_status(
        file_id,
        "failed",
        error_message="File corrupted"
    )
    
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "failed"
    assert file_record["error_message"] == "File corrupted"


def test_list_files(kb_store: KnowledgeBaseStore):
    """Test listing files."""
    # Create multiple files
    kb_store.create_file_record(
        filename="file1.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/1.pdf",
        file_hash="hash1"
    )
    
    file2_id = kb_store.create_file_record(
        filename="file2.pdf",
        file_type="pdf",
        file_size=200,
        file_path="/path/2.pdf",
        file_hash="hash2"
    )
    kb_store.update_file_status(file2_id, "indexed")
    
    # List all files
    all_files = kb_store.list_files()
    assert len(all_files) == 2
    
    # List by status
    pending_files = kb_store.list_files(status="pending")
    assert len(pending_files) == 1
    assert pending_files[0]["filename"] == "file1.pdf"
    
    indexed_files = kb_store.list_files(status="indexed")
    assert len(indexed_files) == 1
    assert indexed_files[0]["filename"] == "file2.pdf"


def test_delete_file(kb_store: KnowledgeBaseStore):
    """Test deleting a file."""
    file_id = kb_store.create_file_record(
        filename="delete_me.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/delete.pdf",
        file_hash="hash_delete"
    )
    
    assert kb_store.get_file(file_id) is not None
    
    kb_store.delete_file(file_id)
    
    assert kb_store.get_file(file_id) is None


def test_transaction_rollback(temp_db: SqliteSessionStore):
    """Test that failed transactions roll back properly."""
    kb_store = KnowledgeBaseStore(temp_db)
    
    # Create a file
    file_id = kb_store.create_file_record(
        filename="test.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/test.pdf",
        file_hash="hash_rollback"
    )
    
    # Attempt to create duplicate hash (should fail)
    with pytest.raises(sqlite3.IntegrityError):
        kb_store.create_file_record(
            filename="duplicate.pdf",
            file_type="pdf",
            file_size=200,
            file_path="/path/dup.pdf",
            file_hash="hash_rollback"  # Same hash - violates UNIQUE
        )
    
    # Original file should still exist
    file_record = kb_store.get_file(file_id)
    assert file_record is not None
    assert file_record["filename"] == "test.pdf"


def test_nested_transaction(temp_db: SqliteSessionStore):
    """Test that nested transactions reuse connection."""
    kb_store = KnowledgeBaseStore(temp_db)
    
    file_id = kb_store.create_file_record(
        filename="nested.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/nested.pdf",
        file_hash="hash_nested"
    )
    
    # Nested read within write should work
    def outer_operation():
        with temp_db.transaction() as db:
            # Inner read (nested)
            file_record = kb_store.get_file(file_id)
            assert file_record is not None
            
            # Continue outer write
            db.execute(
                "UPDATE kb_files SET status = ? WHERE file_id = ?",
                ("indexed", file_id)
            )
    
    temp_db.run_write(outer_operation)
    
    file_record = kb_store.get_file(file_id)
    assert file_record["status"] == "indexed"


def test_double_initialize(tmp_path: Path):
    """Test that double initialization is safe."""
    store = SqliteSessionStore(tmp_path)
    store.initialize(tmp_path)
    
    # Create a file
    kb_store = KnowledgeBaseStore(store)
    file_id = kb_store.create_file_record(
        filename="test.pdf",
        file_type="pdf",
        file_size=100,
        file_path="/path/test.pdf",
        file_hash="hash_double"
    )
    
    # Initialize again
    store.initialize(tmp_path)
    
    # File should still exist
    file_record = kb_store.get_file(file_id)
    assert file_record is not None
    assert file_record["filename"] == "test.pdf"
