"""Tests for knowledge base text chunking."""

from pathlib import Path

import pytest

from nanobot.kb.chunker import ChunkResult, chunk_file, split_text
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


def create_parsed_file(kb_store: KnowledgeBaseStore, text: str) -> str:
    """Helper to create a parsed file with full text."""
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=len(text),
        file_path="/fake/path.txt",
        file_hash="testhash",
        status="pending"
    )
    
    kb_store.save_parsed_result(file_id, text, None, len(text))
    return file_id


def test_split_text_empty():
    """Test splitting empty text."""
    chunks = split_text("", chunk_size=500, overlap=50)
    assert chunks == []


def test_split_text_short():
    """Test text shorter than chunk size."""
    text = "Short text"
    chunks = split_text(text, chunk_size=500, overlap=50)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_text_exact_size():
    """Test text exactly equal to chunk size."""
    text = "x" * 500
    chunks = split_text(text, chunk_size=500, overlap=50)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_text_with_overlap():
    """Test splitting with overlap."""
    text = "x" * 1000
    chunks = split_text(text, chunk_size=500, overlap=50)
    
    # First chunk: 0-500
    # Second chunk: 450-950 (overlap 50)
    # Third chunk: 900-1000 (remainder)
    assert len(chunks) == 3
    assert len(chunks[0]) == 500
    assert len(chunks[1]) == 500
    assert len(chunks[2]) == 100
    
    # Verify overlap
    assert chunks[0][-50:] == chunks[1][:50]
    assert chunks[1][-50:] == chunks[2][:50]


def test_split_text_no_overlap():
    """Test splitting without overlap."""
    text = "x" * 1000
    chunks = split_text(text, chunk_size=500, overlap=0)
    
    assert len(chunks) == 2
    assert len(chunks[0]) == 500
    assert len(chunks[1]) == 500


def test_chunk_file_not_found(kb_store: KnowledgeBaseStore):
    """Test chunking non-existent file."""
    result = chunk_file("nonexistent", kb_store)
    
    assert result.success is False
    assert "not found" in result.error.lower()
    assert result.chunk_count == 0


def test_chunk_file_not_parsed(kb_store: KnowledgeBaseStore):
    """Test chunking file that hasn't been parsed."""
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=100,
        file_path="/fake/path.txt",
        file_hash="testhash",
        status="pending"
    )
    
    result = chunk_file(file_id, kb_store)
    
    assert result.success is False
    assert "not parsed" in result.error.lower()


def test_chunk_file_no_full_text(kb_store: KnowledgeBaseStore):
    """Test chunking when full text is missing."""
    file_id = kb_store.create_file_record(
        filename="test.txt",
        file_type="txt",
        file_size=100,
        file_path="/fake/path.txt",
        file_hash="testhash",
        status="parsed"  # Status set but no full text
    )
    
    result = chunk_file(file_id, kb_store)
    
    assert result.success is False
    assert "full text not found" in result.error.lower()


def test_chunk_file_short_text(kb_store: KnowledgeBaseStore):
    """Test chunking short text (single chunk)."""
    text = "This is a short text."
    file_id = create_parsed_file(kb_store, text)
    
    result = chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    assert result.success is True
    assert result.chunk_count == 1
    assert result.error is None
    
    # Verify chunk stored correctly
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 1
    assert chunks[0]["chunk_text"] == text


def test_chunk_file_long_text(kb_store: KnowledgeBaseStore):
    """Test chunking long text (multiple chunks)."""
    text = "x" * 1500
    file_id = create_parsed_file(kb_store, text)
    
    result = chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    assert result.success is True
    assert result.chunk_count == 4  # 500, 500, 500, 50
    
    # Verify chunks
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) == 4
    assert chunks[0]["chunk_index"] == 1
    assert chunks[1]["chunk_index"] == 2
    assert chunks[2]["chunk_index"] == 3
    assert chunks[3]["chunk_index"] == 4


def test_chunk_file_empty_text(kb_store: KnowledgeBaseStore):
    """Test chunking empty text."""
    file_id = create_parsed_file(kb_store, "")
    
    result = chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    assert result.success is True
    assert result.chunk_count == 0
    
    # No chunks should be created
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) == 0


def test_chunk_file_preserves_full_text(kb_store: KnowledgeBaseStore):
    """Test that chunking preserves chunk_index=0 (full text)."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    
    # Chunk the file
    result = chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    assert result.success is True
    
    # Verify full text still exists at chunk_index=0
    full_text = kb_store.get_parsed_text(file_id)
    assert full_text == text
    
    # Verify chunks start at index 1
    chunks = kb_store.get_chunks(file_id, start_index=1)
    assert all(c["chunk_index"] >= 1 for c in chunks)


def test_chunk_file_duplicate_skipped(kb_store: KnowledgeBaseStore):
    """Test that duplicate chunking is skipped."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    
    # First chunking
    result1 = chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    assert result1.success is True
    count1 = result1.chunk_count
    
    # Second chunking without force
    result2 = chunk_file(file_id, kb_store, chunk_size=500, overlap=50, force=False)
    assert result2.success is True
    assert result2.chunk_count == count1
    
    # Should have same chunks
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) == count1


def test_chunk_file_force_rechunk(kb_store: KnowledgeBaseStore):
    """Test forced re-chunking."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    
    # First chunking with size 500
    result1 = chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    assert result1.success is True
    original_count = result1.chunk_count
    
    # Force re-chunk with different size
    result2 = chunk_file(file_id, kb_store, chunk_size=300, overlap=30, force=True)
    assert result2.success is True
    new_count = result2.chunk_count
    
    # Should have different number of chunks
    assert new_count != original_count
    
    # Verify old chunks replaced
    chunks = kb_store.get_chunks(file_id)
    assert len(chunks) == new_count
    assert all(len(c["chunk_text"]) <= 300 for c in chunks)


def test_chunk_file_rechunk_preserves_full_text(kb_store: KnowledgeBaseStore):
    """Test that re-chunking preserves full text."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    
    # Chunk, then re-chunk
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    chunk_file(file_id, kb_store, chunk_size=300, overlap=30, force=True)
    
    # Full text should still exist
    full_text = kb_store.get_parsed_text(file_id)
    assert full_text == text


def test_delete_file_cascades_to_all_chunks(kb_store: KnowledgeBaseStore):
    """Test that deleting file removes both full text and chunks."""
    text = "x" * 1000
    file_id = create_parsed_file(kb_store, text)
    
    # Chunk the file
    chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    
    # Verify full text and chunks exist
    assert kb_store.get_parsed_text(file_id) is not None
    assert kb_store.count_chunks(file_id, start_index=1) > 0
    
    # Delete file
    kb_store.delete_file(file_id)
    
    # Verify everything deleted
    assert kb_store.get_file(file_id) is None
    assert kb_store.get_parsed_text(file_id) is None
    assert kb_store.count_chunks(file_id, start_index=1) == 0


def test_force_rechunk_with_empty_text_clears_old_chunks(kb_store: KnowledgeBaseStore):
    """Test that force re-chunking with empty text clears old chunks but preserves full text.
    
    Scenario:
    1. File has non-empty text with multiple chunks
    2. Full text updated to empty string (but status still 'parsed')
    3. Force re-chunk is called
    4. Old chunks (chunk_index >= 1) should be deleted
    5. Full text record (chunk_index = 0) should remain with empty string
    """
    # 1. Create file with non-empty text
    text = "x" * 1500
    file_id = create_parsed_file(kb_store, text)
    
    # 2. Initial chunking - creates multiple chunks
    result1 = chunk_file(file_id, kb_store, chunk_size=500, overlap=50)
    assert result1.success is True
    assert result1.chunk_count > 0  # Has chunks
    
    initial_chunk_count = kb_store.count_chunks(file_id, start_index=1)
    assert initial_chunk_count > 0
    
    # 3. Update full text to empty (simulating re-parse)
    kb_store.save_parsed_result(file_id, "", None, 0)
    
    # Verify full text is now empty but still exists at chunk_index=0
    full_text = kb_store.get_parsed_text(file_id)
    assert full_text == ""
    
    # Old chunks still exist
    assert kb_store.count_chunks(file_id, start_index=1) == initial_chunk_count
    
    # 4. Force re-chunk with empty text
    result2 = chunk_file(file_id, kb_store, chunk_size=500, overlap=50, force=True)
    assert result2.success is True
    assert result2.chunk_count == 0  # No new chunks created
    
    # 5. Verify final state
    # Full text record (chunk_index=0) should still exist with empty string
    full_text_after = kb_store.get_parsed_text(file_id)
    assert full_text_after == ""
    
    # Old chunks (chunk_index >= 1) should be deleted
    final_chunk_count = kb_store.count_chunks(file_id, start_index=1)
    assert final_chunk_count == 0
    
    # Verify file record still valid
    record = kb_store.get_file(file_id)
    assert record is not None
    assert record["status"] == "parsed"
    assert record["word_count"] == 0
