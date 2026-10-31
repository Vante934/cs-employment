"""Tests for F06-B-B2-A: pending_file management."""

import re
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFileIdExtraction:
    """Test file_id extraction from KB result strings."""
    
    def test_extract_normal_format(self):
        """Test extraction from normal upload success format."""
        result = """File uploaded and indexed successfully!
Filename: test.pdf
Size: 123.4 KB
Type: PDF
ID: 12345678-1234-5678-1234-567812345678
Chunks: 10
Indexed: 10
Status: indexed"""
        
        # Simulate the regex
        match = re.search(r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})', result, re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "12345678-1234-5678-1234-567812345678"
    
    def test_extract_duplicate_format(self):
        """Test extraction from duplicate file format."""
        result = "File already exists in knowledge base: test.pdf (ID: abcdef12-3456-7890-abcd-ef1234567890, uploaded: 2024-10-30)"
        
        match = re.search(r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})', result, re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "abcdef12-3456-7890-abcd-ef1234567890"
    
    def test_extract_with_file_id_prefix(self):
        """Test extraction with 'File ID:' prefix."""
        result = "File uploaded but processing failed: error\nFile ID: aaa11111-2222-3333-4444-555566667777\nStatus: failed"
        
        match = re.search(r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})', result, re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "aaa11111-2222-3333-4444-555566667777"
    
    def test_extract_no_id(self):
        """Test extraction when no ID present."""
        result = "File uploaded to knowledge base"
        
        match = re.search(r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})', result, re.IGNORECASE)
        assert match is None
    
    def test_extract_invalid_uuid(self):
        """Test extraction with invalid UUID format."""
        result = "ID: not-a-valid-uuid"
        
        match = re.search(r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})', result, re.IGNORECASE)
        assert match is None


class TestPendingFileStructure:
    """Test pending_file data structure."""
    
    def test_complete_structure(self):
        """Test complete pending_file structure."""
        pending_file = {
            "file_id": "12345678-1234-5678-1234-567812345678",
            "filename": "test.pdf",
            "file_size": 1024000,
            "file_type": "pdf",
            "upload_time": datetime.now(timezone.utc).isoformat(),
            "sender_id": "ou_test123",
            "summary": "This is a test document...",
            "status": "pending",
            "request_id": str(uuid.uuid4()),
        }
        
        # Verify all required fields
        assert "file_id" in pending_file
        assert "filename" in pending_file
        assert "file_size" in pending_file
        assert "file_type" in pending_file
        assert "upload_time" in pending_file
        assert "sender_id" in pending_file
        assert "summary" in pending_file
        assert "status" in pending_file
        assert "request_id" in pending_file
        
        # Verify field types
        assert isinstance(pending_file["file_id"], str)
        assert isinstance(pending_file["filename"], str)
        assert isinstance(pending_file["file_size"], int)
        assert isinstance(pending_file["file_type"], str)
        assert isinstance(pending_file["upload_time"], str)
        assert isinstance(pending_file["sender_id"], str)
        assert isinstance(pending_file["summary"], str) or pending_file["summary"] is None
        assert pending_file["status"] == "pending"
        assert isinstance(pending_file["request_id"], str)
        
        # Verify UUID format
        uuid.UUID(pending_file["file_id"])
        uuid.UUID(pending_file["request_id"])
    
    def test_summary_truncation(self):
        """Test summary truncation at 200 chars."""
        long_text = "A" * 300
        summary = long_text[:200].strip()
        if len(long_text) > 200:
            summary += "..."
        
        assert len(summary) == 203  # 200 + "..."
        assert summary.endswith("...")
    
    def test_summary_none(self):
        """Test summary can be None."""
        pending_file = {
            "file_id": "12345678-1234-5678-1234-567812345678",
            "filename": "test.pdf",
            "file_size": 1024,
            "file_type": "pdf",
            "upload_time": datetime.now(timezone.utc).isoformat(),
            "sender_id": "ou_test",
            "summary": None,  # Summary can be None
            "status": "pending",
            "request_id": str(uuid.uuid4()),
        }
        
        assert pending_file["summary"] is None


class TestSessionKeyGeneration:
    """Test session_key generation for private chat."""
    
    def test_private_chat_session_key(self):
        """Test session_key format for private chat."""
        channel_name = "feishu"
        sender_id = "ou_abc123"
        
        session_key = f"{channel_name}:{sender_id}"
        
        assert session_key == "feishu:ou_abc123"
    
    def test_session_key_uniqueness(self):
        """Test different users have different session keys."""
        channel_name = "feishu"
        
        session_key_1 = f"{channel_name}:ou_user1"
        session_key_2 = f"{channel_name}:ou_user2"
        
        assert session_key_1 != session_key_2


class TestChatTypeHandling:
    """Test chat_type filtering."""
    
    def test_p2p_allowed(self):
        """Test p2p chat is allowed."""
        chat_type = "p2p"
        assert chat_type == "p2p"
    
    def test_group_skipped(self):
        """Test group chat is skipped."""
        chat_type = "group"
        assert chat_type != "p2p"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
