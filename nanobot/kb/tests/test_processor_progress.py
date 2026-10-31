"""Tests for KB processor progress callback functionality."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock

import pytest

from nanobot.kb.processor import process_file, ProcessResult
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore
from nanobot.kb.index_manager import IndexManager


class TestProcessFileProgressCallback:
    """Test progress callback in process_file."""

    def test_process_file_signature_has_callback_parameter(self):
        """Process file should have progress_callback parameter."""
        import inspect
        sig = inspect.signature(process_file)
        assert "progress_callback" in sig.parameters
        param = sig.parameters["progress_callback"]
        assert param.default is None  # Optional parameter

    def test_process_file_without_callback(self):
        """Process file should work without callback (backward compatibility)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Setup store
            sqlite_store = SqliteSessionStore(tmpdir_path)
            sqlite_store.initialize(tmpdir_path)
            kb_store = KnowledgeBaseStore(sqlite_store)
            index_manager = IndexManager(kb_store, index_dir=tmpdir_path / "index")
            
            # Create file record (will fail at parse stage, but that's OK)
            file_id = kb_store.create_file_record(
                filename="test.txt",
                file_type="txt",
                file_size=100,
                file_path=str(tmpdir_path / "test.txt"),
                file_hash="hash123",
                status="pending",
            )
            
            # Should not raise even without callback
            result = process_file(file_id, kb_store, index_manager, force=False, progress_callback=None)
            assert isinstance(result, ProcessResult)

    def test_progress_callback_invoked_on_parse_complete(self):
        """Progress callback should be invoked after parsing completes."""
        callback_mock = MagicMock()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Setup
            sqlite_store = SqliteSessionStore(tmpdir_path)
            sqlite_store.initialize(tmpdir_path)
            kb_store = KnowledgeBaseStore(sqlite_store)
            index_manager = IndexManager(kb_store, index_dir=tmpdir_path / "index")
            
            kb_files_dir = tmpdir_path / "kb_files"
            kb_files_dir.mkdir()
            test_file = kb_files_dir / "test.txt"
            test_file.write_text("Test content for knowledge base", encoding="utf-8")
            
            file_id = kb_store.create_file_record(
                filename="test.txt",
                file_type="txt",
                file_size=test_file.stat().st_size,
                file_path=str(test_file),
                file_hash="hash123",
                status="pending",
            )
            
            # Mock all stages to fail early but still trigger parsing callback
            with patch("nanobot.kb.processor.parse_file") as mock_parse, \
                 patch("nanobot.kb.processor.chunk_file") as mock_chunk:
                
                from nanobot.kb.parser import ParseResult
                from nanobot.kb.chunker import ChunkResult
                
                mock_parse.return_value = ParseResult(
                    success=True,
                    text="Test content",
                    error=None,
                    page_count=1,
                    word_count=2,
                )
                
                # Make chunking fail to avoid indexing
                mock_chunk.return_value = ChunkResult(
                    success=False,
                    chunk_count=0,
                    error="Test error",
                )
                
                # Process with callback (will fail at chunking)
                result = process_file(
                    file_id, kb_store, index_manager,
                    force=False,
                    progress_callback=callback_mock
                )
                
                # Check callback was called at least for parsing stage
                calls = [call[0] for call in callback_mock.call_args_list]
                stages = [call[0] for call in calls if call]
                
                # Should have been called with parsing stage
                assert "parsing" in stages, f"Expected 'parsing' in {stages}"

    def test_callback_failure_does_not_break_processing(self):
        """If callback raises, processing should continue."""
        def failing_callback(stage, message):
            raise RuntimeError("Callback error")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            sqlite_store = SqliteSessionStore(tmpdir_path)
            sqlite_store.initialize(tmpdir_path)
            kb_store = KnowledgeBaseStore(sqlite_store)
            index_manager = IndexManager(kb_store, index_dir=tmpdir_path / "index")
            
            # Create file record that will fail early
            file_id = kb_store.create_file_record(
                filename="test.txt",
                file_type="txt",
                file_size=100,
                file_path=str(tmpdir_path / "nonexistent.txt"),
                file_hash="hash123",
                status="pending",
            )
            
            # Should not raise even if callback fails
            result = process_file(
                file_id, kb_store, index_manager,
                force=False,
                progress_callback=failing_callback
            )
            
            # Processing should continue despite callback failure
            assert isinstance(result, ProcessResult)

    def test_callback_messages_do_not_expose_internal_details(self):
        """Callback messages should not expose file paths or technical details."""
        messages = []
        
        def capture_callback(stage, message):
            messages.append((stage, message))
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            sqlite_store = SqliteSessionStore(tmpdir_path)
            sqlite_store.initialize(tmpdir_path)
            kb_store = KnowledgeBaseStore(sqlite_store)
            index_manager = IndexManager(kb_store, index_dir=tmpdir_path / "index")
            
            kb_files_dir = tmpdir_path / "kb_files"
            kb_files_dir.mkdir()
            test_file = kb_files_dir / "test.txt"
            test_file.write_text("Test content", encoding="utf-8")
            
            file_id = kb_store.create_file_record(
                filename="test.txt",
                file_type="txt",
                file_size=test_file.stat().st_size,
                file_path=str(test_file),
                file_hash="hash123",
                status="pending",
            )
            
            with patch("nanobot.kb.processor.parse_file") as mock_parse:
                from nanobot.kb.parser import ParseResult
                mock_parse.return_value = ParseResult(
                    success=True,
                    text="Test content",
                    error=None,
                    page_count=1,
                    word_count=2,
                )
                
                # Process (will fail at chunk or index, but that's fine for this test)
                process_file(
                    file_id, kb_store, index_manager,
                    force=False,
                    progress_callback=capture_callback
                )
                
                # Check messages don't expose internal details
                for stage, message in messages:
                    # Should not contain file paths
                    assert "/" not in message and "\\" not in message
                    # Should not contain exception details
                    assert "exception" not in message.lower()
                    assert "traceback" not in message.lower()
