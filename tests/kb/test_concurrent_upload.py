"""Test concurrent file uploads."""
import asyncio
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.agent.tools.kb_upload import KBUploadTool
from nanobot.kb.embedder import Embedder
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore
import numpy as np


class MockEmbedder:
    """Mock embedder for testing."""
    model_name = "mock-model"
    dimension = 384

    def encode(self, sentences, **kwargs):
        """Return mock embeddings."""
        if isinstance(sentences, str):
            sentences = [sentences]
        
        embeddings = []
        for text in sentences:
            hash_val = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
            embedding = np.array([(hash_val % 100) / 100.0] * self.dimension, dtype=np.float32)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            embeddings.append(embedding)
        
        return np.array(embeddings) if len(embeddings) > 1 else embeddings[0]


@pytest.mark.asyncio
async def test_concurrent_uploads(tmp_path: Path):
    """Test multiple files uploaded concurrently."""
    # Setup
    temp_db = SqliteSessionStore(tmp_path)
    temp_db.initialize(tmp_path)
    kb_store = KnowledgeBaseStore(temp_db)
    
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    kb_files_dir = tmp_path / "kb" / "files"
    kb_files_dir.mkdir(parents=True)
    
    tool = KBUploadTool(kb_store, media_dir, kb_files_dir, max_file_size_mb=10)
    
    # Create test files
    files = []
    for i in range(3):
        file_path = media_dir / f"test{i}.txt"
        file_path.write_text(f"Content for file {i}")
        files.append(file_path)
    
    # Mock dependencies
    mock_embedder = Embedder(model=MockEmbedder())
    
    with patch("nanobot.kb.parser.get_runtime_subdir") as mock_parser_subdir, \
         patch("nanobot.kb.index_manager.get_runtime_subdir") as mock_index_subdir, \
         patch("nanobot.kb.parser.parse_file") as mock_parser, \
         patch("nanobot.kb.index_manager.get_embedder") as mock_get_embedder:
        
        mock_parser_subdir.return_value = tmp_path / "kb"
        mock_index_subdir.return_value = tmp_path / "kb"
        mock_get_embedder.return_value = mock_embedder
        
        def mock_parse(file_id, kb_store, force=False):
            file_record = kb_store.get_file(file_id)
            if not file_record:
                return type('ParseResult', (), {
                    'success': False,
                    'error': 'File not found'
                })()
            
            file_path = Path(file_record["file_path"])
            content = file_path.read_text()
            return type('ParseResult', (), {
                'success': True,
                'text': content,
                'page_count': 1,
                'word_count': len(content.split()),
                'error': None
            })()
        
        mock_parser.side_effect = mock_parse
        
        # Upload concurrently
        tasks = [tool.execute(file_path=str(f)) for f in files]
        results = await asyncio.gather(*tasks)
    
    # Verify all succeeded
    for i, result in enumerate(results):
        assert not getattr(result, "is_error", False), f"File {i} failed: {result}"
    
    # Verify database records
    all_files = kb_store.list_files()
    assert len(all_files) == 3, f"Expected 3 files, got {len(all_files)}"
    
    # Verify all files are indexed
    indexed_count = sum(1 for f in all_files if f["status"] == "indexed")
    assert indexed_count == 3, f"Expected 3 indexed files, got {indexed_count}"
    
    print(f"✓ Concurrent upload test passed: {len(all_files)} files uploaded and indexed")


if __name__ == "__main__":
    import sys
    asyncio.run(test_concurrent_uploads(Path("test_tmp")))
    print("All concurrent tests passed!")
