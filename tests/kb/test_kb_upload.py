"""Test KB upload tool."""

import hashlib
from pathlib import Path

import pytest

from nanobot.agent.tools.kb_upload import KBUploadTool, SUPPORTED_EXTENSIONS
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
def media_dir(tmp_path: Path) -> Path:
    """Create a media directory with test files."""
    media = tmp_path / "media" / "feishu"
    media.mkdir(parents=True)
    return media


@pytest.fixture
def kb_files_dir(tmp_path: Path) -> Path:
    """Create a KB files directory."""
    kb_dir = tmp_path / "kb" / "files"
    kb_dir.mkdir(parents=True)
    return kb_dir


@pytest.fixture
def kb_upload_tool(kb_store: KnowledgeBaseStore, media_dir: Path, kb_files_dir: Path) -> KBUploadTool:
    """Create KBUploadTool instance."""
    return KBUploadTool(
        kb_store=kb_store,
        media_dir=media_dir,
        kb_files_dir=kb_files_dir,
        max_file_size_mb=1,  # 1 MB for testing
    )


def create_test_file(media_dir: Path, filename: str, content: bytes) -> Path:
    """Create a test file in media directory."""
    file_path = media_dir / filename
    file_path.write_bytes(content)
    return file_path


@pytest.mark.asyncio
async def test_upload_pdf_success(kb_upload_tool: KBUploadTool, media_dir: Path):
    """Test successful PDF upload."""
    file_path = create_test_file(media_dir, "test.pdf", b"PDF content")
    
    result = await kb_upload_tool.execute(file_path=str(file_path))
    
    assert not getattr(result, "is_error", False)
    result_str = str(result)
    assert "uploaded successfully" in result_str.lower()
    assert "test.pdf" in result_str
    assert "pending" in result_str.lower()


@pytest.mark.asyncio
async def test_upload_all_supported_types(kb_upload_tool: KBUploadTool, media_dir: Path):
    """Test all supported file types."""
    for ext in SUPPORTED_EXTENSIONS:
        filename = f"test{ext}"
        file_path = create_test_file(media_dir, filename, f"Content for {ext}".encode())
        
        result = await kb_upload_tool.execute(file_path=str(file_path))
        result_str = str(result)
        
        assert not getattr(result, "is_error", False), f"Failed for {ext}: {result_str}"
        assert filename in result_str


@pytest.mark.asyncio
async def test_upload_unsupported_type(kb_upload_tool: KBUploadTool, media_dir: Path):
    """Test rejection of unsupported file types."""
    file_path = create_test_file(media_dir, "test.exe", b"executable")
    
    result = await kb_upload_tool.execute(file_path=str(file_path))
    
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "unsupported file type" in result_str.lower()
    assert ".exe" in result_str


@pytest.mark.asyncio
async def test_upload_file_too_large(kb_upload_tool: KBUploadTool, media_dir: Path):
    """Test rejection of oversized files."""
    # Create 2 MB file (exceeds 1 MB limit)
    large_content = b"x" * (2 * 1024 * 1024)
    file_path = create_test_file(media_dir, "large.pdf", large_content)
    
    result = await kb_upload_tool.execute(file_path=str(file_path))
    
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "too large" in result_str.lower()
    assert "1 mb" in result_str.lower()


@pytest.mark.asyncio
async def test_upload_duplicate_file(kb_upload_tool: KBUploadTool, media_dir: Path):
    """Test duplicate detection."""
    file_path = create_test_file(media_dir, "duplicate.pdf", b"Same content")
    
    # First upload
    result1 = await kb_upload_tool.execute(file_path=str(file_path))
    assert not getattr(result1, "is_error", False)
    
    # Second upload with same content
    result2 = await kb_upload_tool.execute(file_path=str(file_path))
    result2_str = str(result2)
    assert not getattr(result2, "is_error", False)
    assert "already exists" in result2_str.lower()
    assert "duplicate.pdf" in result2_str


@pytest.mark.asyncio
async def test_upload_file_not_found(kb_upload_tool: KBUploadTool):
    """Test handling of non-existent files."""
    result = await kb_upload_tool.execute(file_path="/nonexistent/file.pdf")
    
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "not found" in result_str.lower() or "inaccessible" in result_str.lower()


@pytest.mark.asyncio
async def test_upload_directory_rejected(kb_upload_tool: KBUploadTool, media_dir: Path):
    """Test rejection of directories."""
    dir_path = media_dir / "subdir"
    dir_path.mkdir()
    
    result = await kb_upload_tool.execute(file_path=str(dir_path))
    
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "regular file" in result_str.lower()


@pytest.mark.asyncio
async def test_upload_symlink_rejected(kb_upload_tool: KBUploadTool, media_dir: Path, tmp_path: Path):
    """Test rejection of symbolic links."""
    real_file = create_test_file(media_dir, "real.pdf", b"content")
    symlink = media_dir / "link.pdf"
    
    try:
        symlink.symlink_to(real_file)
    except OSError:
        pytest.skip("Symlinks not supported on this platform")
    
    result = await kb_upload_tool.execute(file_path=str(symlink))
    
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "symlink" in result_str.lower() or "not allowed" in result_str.lower()


@pytest.mark.asyncio
async def test_upload_path_traversal_rejected(kb_upload_tool: KBUploadTool, tmp_path: Path):
    """Test rejection of files outside media directory."""
    # Create file outside media dir
    outside_file = tmp_path / "outside.pdf"
    outside_file.write_bytes(b"content")
    
    result = await kb_upload_tool.execute(file_path=str(outside_file))
    
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "media directory" in result_str.lower()


@pytest.mark.asyncio
async def test_upload_relative_path_escape_rejected(kb_upload_tool: KBUploadTool, media_dir: Path, tmp_path: Path):
    """Test rejection of relative path escape attempts."""
    # Create file outside media dir
    outside_file = tmp_path / "escape.pdf"
    outside_file.write_bytes(b"content")
    
    # Try path traversal
    evil_path = str(media_dir / ".." / ".." / "escape.pdf")
    
    result = await kb_upload_tool.execute(file_path=evil_path)
    
    assert getattr(result, "is_error", False)


@pytest.mark.asyncio
async def test_upload_stores_with_correct_hash(kb_upload_tool: KBUploadTool, media_dir: Path, kb_files_dir: Path):
    """Test that uploaded file is stored with correct hash."""
    content = b"Test content for hashing"
    expected_hash = hashlib.sha256(content).hexdigest()
    file_path = create_test_file(media_dir, "hash_test.pdf", content)
    
    result = await kb_upload_tool.execute(file_path=str(file_path))
    assert not getattr(result, "is_error", False)
    
    # Check file was stored with hash as filename
    stored_file = kb_files_dir / f"{expected_hash}.pdf"
    assert stored_file.exists()
    assert stored_file.read_bytes() == content


@pytest.mark.asyncio
async def test_upload_creates_database_record(kb_upload_tool: KBUploadTool, kb_store: KnowledgeBaseStore, media_dir: Path):
    """Test that database record is created correctly."""
    content = b"Database test content"
    file_path = create_test_file(media_dir, "db_test.txt", content)
    file_hash = hashlib.sha256(content).hexdigest()
    
    result = await kb_upload_tool.execute(file_path=str(file_path))
    assert not getattr(result, "is_error", False)
    
    # Verify database record
    record = kb_store.get_file_by_hash(file_hash)
    assert record is not None
    assert record["filename"] == "db_test.txt"
    assert record["file_type"] == "txt"
    assert record["file_size"] == len(content)
    assert record["status"] == "pending"
    assert record["file_hash"] == file_hash


@pytest.mark.asyncio
async def test_upload_rollback_on_db_failure(kb_upload_tool: KBUploadTool, media_dir: Path, kb_files_dir: Path, monkeypatch):
    """Test that file is deleted if database insert fails."""
    content = b"Rollback test"
    file_path = create_test_file(media_dir, "rollback.pdf", content)
    file_hash = hashlib.sha256(content).hexdigest()
    
    # Mock database failure
    def failing_create(*args, **kwargs):
        raise RuntimeError("Database error")
    
    monkeypatch.setattr(kb_upload_tool._kb_store, "create_file_record", failing_create)
    
    result = await kb_upload_tool.execute(file_path=str(file_path))
    
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "database" in result_str.lower()
    
    # Verify file was cleaned up
    stored_file = kb_files_dir / f"{file_hash}.pdf"
    assert not stored_file.exists()


@pytest.mark.asyncio
async def test_upload_same_content_different_names(kb_upload_tool: KBUploadTool, media_dir: Path):
    """Test that files with same content but different names are detected as duplicates."""
    content = b"Identical content"
    
    file1 = create_test_file(media_dir, "file1.pdf", content)
    file2 = create_test_file(media_dir, "file2.pdf", content)
    
    result1 = await kb_upload_tool.execute(file_path=str(file1))
    assert not getattr(result1, "is_error", False)
    result1_str = str(result1)
    assert "uploaded successfully" in result1_str.lower()
    
    result2 = await kb_upload_tool.execute(file_path=str(file2))
    result2_str = str(result2)
    assert not getattr(result2, "is_error", False)
    assert "already exists" in result2_str.lower()
    assert "file1.pdf" in result2_str  # Should reference original file


@pytest.mark.asyncio
async def test_upload_preserves_original_filename(kb_upload_tool: KBUploadTool, kb_store: KnowledgeBaseStore, media_dir: Path):
    """Test that original filename is preserved in database."""
    file_path = create_test_file(media_dir, "original_name.xlsx", b"Excel content")
    
    result = await kb_upload_tool.execute(file_path=str(file_path))
    assert not getattr(result, "is_error", False)
    
    # Check all files in KB
    files = kb_store.list_files()
    assert len(files) == 1
    assert files[0]["filename"] == "original_name.xlsx"


@pytest.mark.asyncio
async def test_upload_validates_existing_file_hash(kb_upload_tool: KBUploadTool, media_dir: Path, kb_files_dir: Path):
    """Test that existing files are validated by hash."""
    content = b"Original content"
    file_hash = hashlib.sha256(content).hexdigest()
    file_path = create_test_file(media_dir, "validate.pdf", content)
    
    # First upload
    result1 = await kb_upload_tool.execute(file_path=str(file_path))
    assert not getattr(result1, "is_error", False)
    
    # Verify file was stored
    stored_file = kb_files_dir / f"{file_hash}.pdf"
    assert stored_file.exists()
    
    # Second upload with same content - should validate and reuse
    result2 = await kb_upload_tool.execute(file_path=str(file_path))
    result2_str = str(result2)
    assert not getattr(result2, "is_error", False)
    assert "already exists" in result2_str.lower()


@pytest.mark.asyncio
async def test_upload_detects_hash_collision(kb_upload_tool: KBUploadTool, media_dir: Path, kb_files_dir: Path):
    """Test that hash collision is detected and rejected."""
    content = b"Test content for collision"
    file_hash = hashlib.sha256(content).hexdigest()
    
    # Pre-create a corrupted file in KB storage (simulating external corruption)
    stored_file = kb_files_dir / f"{file_hash}.pdf"
    stored_file.write_bytes(b"Corrupted different content")
    
    # Try to upload a file with the same hash
    file_path = create_test_file(media_dir, "collision.pdf", content)
    result = await kb_upload_tool.execute(file_path=str(file_path))
    
    # Should detect collision during copy
    assert getattr(result, "is_error", False)
    result_str = str(result)
    assert "hash collision" in result_str.lower() or "failed to copy" in result_str.lower()
    
    # Verify original file was not overwritten
    assert stored_file.read_bytes() == b"Corrupted different content"


@pytest.mark.asyncio
async def test_upload_concurrent_same_file(kb_upload_tool: KBUploadTool, media_dir: Path, kb_files_dir: Path):
    """Test that concurrent uploads of same file are handled safely."""
    import asyncio
    
    content = b"Concurrent upload content"
    file_hash = hashlib.sha256(content).hexdigest()
    file_path = create_test_file(media_dir, "concurrent.pdf", content)
    
    # Simulate concurrent uploads
    results = await asyncio.gather(
        kb_upload_tool.execute(file_path=str(file_path)),
        kb_upload_tool.execute(file_path=str(file_path)),
        kb_upload_tool.execute(file_path=str(file_path)),
    )
    
    # At least one should succeed
    success_count = sum(1 for r in results if not getattr(r, "is_error", False))
    assert success_count >= 1
    
    # Verify file exists and has correct content
    stored_file = kb_files_dir / f"{file_hash}.pdf"
    assert stored_file.exists()
    assert stored_file.read_bytes() == content
    
    # Verify only one DB record exists
    files = kb_upload_tool._kb_store.list_files()
    matching_files = [f for f in files if f["file_hash"] == file_hash]
    assert len(matching_files) == 1
