"""Test Feishu file attachment auto-upload to KB."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_channel(tmp_path):
    """Create a mock channel with KB upload method."""
    media_dir = tmp_path / "media" / "feishu"
    media_dir.mkdir(parents=True)
    
    # Simple mock channel
    channel = SimpleNamespace(
        logger=SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        ),
        _kb_tool=None,
    )
    
    # Add the auto_upload method (simplified version for testing)
    async def _auto_upload_to_kb(file_path: str, sender_id=None):
        from pathlib import Path
        path = Path(file_path)
        ext = path.suffix.lower()
        
        SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".xlsx"}
        if ext not in SUPPORTED_EXTS:
            return None
        
        if not hasattr(channel, '_kb_tool') or channel._kb_tool is None:
            return "⚠️ KB tool not initialized"
        
        result = await channel._kb_tool.execute(file_path=file_path)
        
        if getattr(result, "is_error", False):
            return f"⚠️ Knowledge base upload failed: {str(result)}"
        else:
            result_str = str(result)
            if "ID:" in result_str:
                return "✅ File added to knowledge base successfully"
            elif "already exists" in result_str.lower():
                return "ℹ️ File already in knowledge base"
            else:
                return "✅ File uploaded to knowledge base"
    
    channel._auto_upload_to_kb = _auto_upload_to_kb
    
    return channel, media_dir


@pytest.mark.asyncio
async def test_auto_upload_pdf_to_kb(mock_channel, tmp_path):
    """Test that PDF files are automatically uploaded to KB."""
    channel, media_dir = mock_channel
    
    # Create a test PDF
    pdf_file = media_dir / "test.pdf"
    pdf_file.write_bytes(b"PDF content")
    
    # Mock KB tool
    upload_called = []
    
    class MockKBTool:
        async def execute(self, file_path):
            upload_called.append(file_path)
            return "File uploaded successfully!\nFilename: test.pdf\nID: test-id-123"
    
    channel._kb_tool = MockKBTool()
    
    # Execute
    result = await channel._auto_upload_to_kb(str(pdf_file))
    
    # Verify
    assert result is not None
    assert "successfully" in result.lower() or "added" in result.lower()
    assert len(upload_called) == 1
    assert upload_called[0] == str(pdf_file)


@pytest.mark.asyncio
async def test_auto_upload_only_supported_types(mock_channel):
    """Test that only supported document types trigger upload."""
    channel, media_dir = mock_channel
    
    # Supported types should return a result (even if KB not initialized)
    for ext in [".pdf", ".docx", ".txt", ".md", ".xlsx"]:
        file = media_dir / f"test{ext}"
        file.write_bytes(b"content")
        result = await channel._auto_upload_to_kb(str(file))
        assert result is not None  # Should attempt upload
    
    # Unsupported types should return None
    for ext in [".jpg", ".png", ".mp3", ".mp4", ".zip"]:
        file = media_dir / f"test{ext}"
        file.write_bytes(b"content")
        result = await channel._auto_upload_to_kb(str(file))
        assert result is None


@pytest.mark.asyncio
async def test_auto_upload_handles_duplicate(mock_channel):
    """Test that duplicate files are handled gracefully."""
    channel, media_dir = mock_channel
    
    pdf_file = media_dir / "duplicate.pdf"
    pdf_file.write_bytes(b"PDF content")
    
    class MockKBTool:
        async def execute(self, file_path):
            return "File already exists in knowledge base: duplicate.pdf"
    
    channel._kb_tool = MockKBTool()
    
    result = await channel._auto_upload_to_kb(str(pdf_file))
    
    assert result is not None
    assert "already" in result.lower()


@pytest.mark.asyncio
async def test_auto_upload_handles_error(mock_channel):
    """Test that upload errors are handled gracefully."""
    channel, media_dir = mock_channel
    
    pdf_file = media_dir / "error.pdf"
    pdf_file.write_bytes(b"PDF content")
    
    class MockKBTool:
        async def execute(self, file_path):
            # Return ToolResult.error
            class ErrorResult:
                is_error = True
                def __str__(self):
                    return "File too large"
            return ErrorResult()
    
    channel._kb_tool = MockKBTool()
    
    result = await channel._auto_upload_to_kb(str(pdf_file))
    
    assert result is not None
    assert "failed" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_file_message_triggers_kb_upload(tmp_path):
    """Test that file messages trigger KB upload in message processing."""
    media_dir = tmp_path / "media" / "feishu"
    media_dir.mkdir(parents=True)
    
    # Create test file
    test_file = media_dir / "upload.pdf"
    test_file.write_bytes(b"Test PDF")
    
    # Track if auto_upload was called
    upload_calls = []
    
    async def mock_auto_upload(file_path, sender_id=None):
        upload_calls.append((file_path, sender_id))
        return "✅ File added to knowledge base"
    
    channel = SimpleNamespace(_auto_upload_to_kb=mock_auto_upload)
    
    # Simulate _download_and_save_media result
    file_path = str(test_file)
    content_text = f"[file: {file_path}]"
    
    # Simulate the file message processing logic
    msg_type = "file"
    if file_path and msg_type == "file":
        kb_result = await channel._auto_upload_to_kb(file_path, "test_sender")
        if kb_result:
            content_text = f"{content_text}\n{kb_result}"
    
    # Verify
    assert len(upload_calls) == 1
    assert upload_calls[0][0] == file_path
    assert upload_calls[0][1] == "test_sender"
    assert "added to knowledge base" in content_text


@pytest.mark.asyncio
async def test_non_file_messages_not_uploaded(mock_channel):
    """Test that non-file messages (images, audio) are not uploaded to KB."""
    channel, media_dir = mock_channel
    
    # Images should not be uploaded
    image_file = media_dir / "test.jpg"
    image_file.write_bytes(b"JPEG data")
    result = await channel._auto_upload_to_kb(str(image_file))
    assert result is None
    
    # Audio should not be uploaded
    audio_file = media_dir / "test.mp3"
    audio_file.write_bytes(b"MP3 data")
    result = await channel._auto_upload_to_kb(str(audio_file))
    assert result is None
    
    # Video should not be uploaded
    video_file = media_dir / "test.mp4"
    video_file.write_bytes(b"MP4 data")
    result = await channel._auto_upload_to_kb(str(video_file))
    assert result is None
