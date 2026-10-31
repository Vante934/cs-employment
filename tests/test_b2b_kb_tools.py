"""Tests for F06-B-B2-B: KB file access tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np


class TestKBToolsBasicStructure:
    """Test basic tool structure and registration."""
    
    def test_kb_search_tool_name(self):
        """Test kb_search tool name."""
        from nanobot.agent.tools.kb_access import KBSearchTool
        
        sessions = MagicMock()
        tool = KBSearchTool(sessions)
        
        assert tool.name == "kb_search"
    
    def test_kb_read_tool_name(self):
        """Test kb_read tool name."""
        from nanobot.agent.tools.kb_access import KBReadTool
        
        sessions = MagicMock()
        tool = KBReadTool(sessions)
        
        assert tool.name == "kb_read"
    
    def test_kb_search_tool_description(self):
        """Test kb_search has description."""
        from nanobot.agent.tools.kb_access import KBSearchTool
        
        sessions = MagicMock()
        tool = KBSearchTool(sessions)
        
        assert "search" in tool.description.lower()
        assert "file" in tool.description.lower()
    
    def test_kb_read_tool_description(self):
        """Test kb_read has description."""
        from nanobot.agent.tools.kb_access import KBReadTool
        
        sessions = MagicMock()
        tool = KBReadTool(sessions)
        
        assert "read" in tool.description.lower()
        assert "file" in tool.description.lower()
    
    def test_kb_search_parameters(self):
        """Test kb_search parameters schema."""
        from nanobot.agent.tools.kb_access import KBSearchTool
        
        sessions = MagicMock()
        tool = KBSearchTool(sessions)
        params = tool.parameters
        
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert "query" in params["required"]
    
    def test_kb_read_parameters(self):
        """Test kb_read parameters schema."""
        from nanobot.agent.tools.kb_access import KBReadTool
        
        sessions = MagicMock()
        tool = KBReadTool(sessions)
        params = tool.parameters
        
        assert params["type"] == "object"
        assert "part_index" in params["properties"]
    
    def test_kb_search_is_read_only(self):
        """Test kb_search is read-only."""
        from nanobot.agent.tools.kb_access import KBSearchTool
        
        sessions = MagicMock()
        tool = KBSearchTool(sessions)
        
        assert tool.read_only is True
    
    def test_kb_read_is_read_only(self):
        """Test kb_read is read-only."""
        from nanobot.agent.tools.kb_access import KBReadTool
        
        sessions = MagicMock()
        tool = KBReadTool(sessions)
        
        assert tool.read_only is True


class TestPermissionValidation:
    """Test permission validation logic."""
    
    @pytest.mark.asyncio
    async def test_no_request_context(self):
        """Test error when no RequestContext."""
        from nanobot.agent.tools.kb_access import KBSearchTool
        from nanobot.agent.tools.context import current_request_context
        
        # Ensure no context
        assert current_request_context() is None
        
        sessions = MagicMock()
        tool = KBSearchTool(sessions)
        
        result = await tool.execute(query="test")
        
        assert "Error:" in result
        assert "context" in result.lower()
    
    @pytest.mark.asyncio
    async def test_empty_query_rejected(self):
        """Test empty query is rejected."""
        from nanobot.agent.tools.kb_access import KBSearchTool
        
        sessions = MagicMock()
        tool = KBSearchTool(sessions)
        
        result = await tool.execute(query="")
        
        assert "Error:" in result
        assert "empty" in result.lower()
    
    @pytest.mark.asyncio
    async def test_negative_part_index_rejected(self):
        """Test negative part_index is rejected."""
        from nanobot.agent.tools.kb_access import KBReadTool
        
        sessions = MagicMock()
        tool = KBReadTool(sessions)
        
        result = await tool.execute(part_index=-1)
        
        assert "Error:" in result
        assert "non-negative" in result.lower()


class TestFileScopedSearch:
    """Test file-scoped search functionality."""
    
    def test_file_scope_top_k_correct(self):
        """Test file-scoped search returns correct Top-K from target file only."""
        from nanobot.kb.index_manager import IndexManager
        
        # Mock dependencies
        mock_kb_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.dimension = 384
        
        # Create manager with mocks
        manager = IndexManager(mock_kb_store, embedder=mock_embedder)
        
        # Mock vector index
        mock_vector_index = MagicMock()
        manager._vector_index = mock_vector_index
        
        # Setup: file_a has 3 chunks with known similarities
        file_a_id = "file-a"
        file_a_chunks = [
            f"{file_a_id}_chunk_1",
            f"{file_a_id}_chunk_2",
            f"{file_a_id}_chunk_3"
        ]
        
        # Create normalized test vectors
        query_vec = np.array([1.0] + [0.0] * 383, dtype=np.float32)
        vec_high = np.array([0.9] + [0.0] * 383, dtype=np.float32)  # sim=0.9
        vec_med = np.array([0.5] + [0.0] * 383, dtype=np.float32)   # sim=0.5
        vec_low = np.array([0.3] + [0.0] * 383, dtype=np.float32)   # sim=0.3
        
        # Mock chunk_ids_matching to return file_a chunks
        mock_vector_index.chunk_ids_matching.return_value = file_a_chunks
        
        # Mock get_vector to return vectors in order
        def mock_get_vector(chunk_id):
            if chunk_id == file_a_chunks[0]:
                return vec_med
            elif chunk_id == file_a_chunks[1]:
                return vec_high
            elif chunk_id == file_a_chunks[2]:
                return vec_low
            return None
        
        mock_vector_index.get_vector.side_effect = mock_get_vector
        
        # Mock embedder to return query vector
        mock_embedder.embed.return_value = query_vec
        
        # Mock KB store to return chunk metadata
        mock_kb_store.get_chunks_by_ids.return_value = {
            file_a_chunks[0]: {
                "chunk_id": file_a_chunks[0],
                "chunk_text": "Medium similarity content",
                "chunk_index": 1,
                "file_id": file_a_id,
                "filename": "file_a.txt",
                "file_type": "text/plain",
                "source_page": None,
                "source_section": None
            },
            file_a_chunks[1]: {
                "chunk_id": file_a_chunks[1],
                "chunk_text": "High similarity content",
                "chunk_index": 2,
                "file_id": file_a_id,
                "filename": "file_a.txt",
                "file_type": "text/plain",
                "source_page": None,
                "source_section": None
            },
            file_a_chunks[2]: {
                "chunk_id": file_a_chunks[2],
                "chunk_text": "Low similarity content",
                "chunk_index": 3,
                "file_id": file_a_id,
                "filename": "file_a.txt",
                "file_type": "text/plain",
                "source_page": None,
                "source_section": None
            }
        }
        
        # Execute file-scoped search
        response = manager.search("query", top_k=2, file_id=file_a_id)
        
        # Verify
        assert response.success
        assert len(response.results) == 2  # top_k=2
        assert response.candidate_count == 3  # 3 chunks passed min_similarity
        
        # Verify descending order by similarity
        assert response.results[0].similarity > response.results[1].similarity
        assert abs(response.results[0].similarity - 0.9) < 0.01
        assert abs(response.results[1].similarity - 0.5) < 0.01
        
        # Verify only file_a chunks
        assert all(r.file_id == file_a_id for r in response.results)
        assert response.results[0].chunk_text == "High similarity content"
        assert response.results[1].chunk_text == "Medium similarity content"
    
    def test_global_search_with_file_id_none(self):
        """Test global search behavior when file_id is None."""
        from nanobot.kb.index_manager import IndexManager
        from nanobot.kb.index import SearchResult as VectorSearchResult
        
        # Mock dependencies
        mock_kb_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.dimension = 384
        
        manager = IndexManager(mock_kb_store, embedder=mock_embedder)
        
        # Mock vector index for global search
        mock_vector_index = MagicMock()
        manager._vector_index = mock_vector_index
        
        query_vec = np.array([1.0] + [0.0] * 383, dtype=np.float32)
        mock_embedder.embed.return_value = query_vec
        
        # Mock FAISS search results (global)
        mock_vector_index.search.return_value = [
            VectorSearchResult(chunk_id="file1_chunk_1", similarity=0.9, distance=0.9),
            VectorSearchResult(chunk_id="file2_chunk_1", similarity=0.8, distance=0.8)
        ]
        mock_vector_index.count.return_value = 100
        
        # Mock KB store
        mock_kb_store.get_chunks_by_ids.return_value = {
            "file1_chunk_1": {
                "chunk_id": "file1_chunk_1",
                "chunk_text": "Content from file 1",
                "chunk_index": 1,
                "file_id": "file-1",
                "filename": "file1.txt",
                "file_type": "text/plain",
                "source_page": None,
                "source_section": None
            },
            "file2_chunk_1": {
                "chunk_id": "file2_chunk_1",
                "chunk_text": "Content from file 2",
                "chunk_index": 1,
                "file_id": "file-2",
                "filename": "file2.txt",
                "file_type": "text/plain",
                "source_page": None,
                "source_section": None
            }
        }
        
        # Execute global search (file_id=None)
        response = manager.search("query", top_k=5, file_id=None)
        
        # Verify
        assert response.success
        assert len(response.results) == 2
        
        # Verify results from multiple files
        file_ids = {r.file_id for r in response.results}
        assert len(file_ids) == 2
        assert "file-1" in file_ids
        assert "file-2" in file_ids
        
        # Verify FAISS search was called (not file-scoped search)
        mock_vector_index.search.assert_called_once()
    
    def test_min_similarity_in_file_scope(self):
        """Test min_similarity correctly filters in file-scoped search."""
        from nanobot.kb.index_manager import IndexManager
        
        mock_kb_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.dimension = 384
        
        manager = IndexManager(mock_kb_store, embedder=mock_embedder)
        mock_vector_index = MagicMock()
        manager._vector_index = mock_vector_index
        
        file_id = "test-file"
        chunks = [f"{file_id}_chunk_{i}" for i in range(1, 4)]
        
        query_vec = np.array([1.0] + [0.0] * 383, dtype=np.float32)
        vec1 = np.array([0.8] + [0.0] * 383, dtype=np.float32)  # sim=0.8
        vec2 = np.array([0.5] + [0.0] * 383, dtype=np.float32)  # sim=0.5
        vec3 = np.array([0.3] + [0.0] * 383, dtype=np.float32)  # sim=0.3
        
        mock_vector_index.chunk_ids_matching.return_value = chunks
        
        def mock_get_vector(chunk_id):
            if chunk_id == chunks[0]:
                return vec1
            elif chunk_id == chunks[1]:
                return vec2
            elif chunk_id == chunks[2]:
                return vec3
            return None
        
        mock_vector_index.get_vector.side_effect = mock_get_vector
        mock_embedder.embed.return_value = query_vec
        
        mock_kb_store.get_chunks_by_ids.return_value = {
            chunks[0]: {
                "chunk_id": chunks[0], "chunk_text": "High", "chunk_index": 1,
                "file_id": file_id, "filename": "test.txt", "file_type": "text/plain"
            },
            chunks[1]: {
                "chunk_id": chunks[1], "chunk_text": "Medium", "chunk_index": 2,
                "file_id": file_id, "filename": "test.txt", "file_type": "text/plain"
            }
        }
        
        # Search with min_similarity=0.4 (should filter out sim=0.3)
        response = manager.search("query", top_k=5, min_similarity=0.4, file_id=file_id)
        
        assert response.success
        assert len(response.results) == 2  # Only 2 passed threshold
        assert response.candidate_count == 2  # 2 candidates passed filter
        assert all(r.similarity >= 0.4 for r in response.results)
        assert response.results[0].similarity > 0.7
        assert response.results[1].similarity > 0.4
    
    def test_similarity_values_and_sorting(self):
        """Test similarity values are correct and results are sorted."""
        from nanobot.kb.index_manager import IndexManager
        
        mock_kb_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.dimension = 384
        
        manager = IndexManager(mock_kb_store, embedder=mock_embedder)
        mock_vector_index = MagicMock()
        manager._vector_index = mock_vector_index
        
        file_id = "test-file"
        chunks = [f"{file_id}_chunk_{i}" for i in range(1, 5)]
        
        # Known vectors for predictable inner products
        query_vec = np.array([1.0, 0.0, 0.0] + [0.0] * 381, dtype=np.float32)
        vec1 = np.array([0.95, 0.0, 0.0] + [0.0] * 381, dtype=np.float32)  # sim=0.95
        vec2 = np.array([0.7, 0.0, 0.0] + [0.0] * 381, dtype=np.float32)   # sim=0.7
        vec3 = np.array([0.4, 0.0, 0.0] + [0.0] * 381, dtype=np.float32)   # sim=0.4
        vec4 = np.array([0.1, 0.0, 0.0] + [0.0] * 381, dtype=np.float32)   # sim=0.1
        
        mock_vector_index.chunk_ids_matching.return_value = chunks
        
        vectors = [vec1, vec2, vec3, vec4]
        def mock_get_vector(chunk_id):
            idx = chunks.index(chunk_id)
            return vectors[idx]
        
        mock_vector_index.get_vector.side_effect = mock_get_vector
        mock_embedder.embed.return_value = query_vec
        
        mock_kb_store.get_chunks_by_ids.return_value = {
            chunks[i]: {
                "chunk_id": chunks[i], "chunk_text": f"Content {i+1}", "chunk_index": i+1,
                "file_id": file_id, "filename": "test.txt", "file_type": "text/plain"
            }
            for i in range(4)
        }
        
        response = manager.search("query", top_k=3, file_id=file_id)
        
        assert response.success
        assert len(response.results) == 3
        
        # Verify exact similarity values (inner product)
        assert abs(response.results[0].similarity - 0.95) < 0.01
        assert abs(response.results[1].similarity - 0.7) < 0.01
        assert abs(response.results[2].similarity - 0.4) < 0.01
        
        # Verify descending sort
        for i in range(len(response.results) - 1):
            assert response.results[i].similarity >= response.results[i+1].similarity
        
        # Verify in range [-1, 1]
        assert all(-1.0 <= r.similarity <= 1.0 for r in response.results)
    
    def test_search_result_fields_complete(self):
        """Test SearchResult contains all required fields."""
        from nanobot.kb.index_manager import IndexManager
        
        mock_kb_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.dimension = 384
        
        manager = IndexManager(mock_kb_store, embedder=mock_embedder)
        mock_vector_index = MagicMock()
        manager._vector_index = mock_vector_index
        
        file_id = "test-file"
        chunk_id = f"{file_id}_chunk_1"
        
        query_vec = np.array([1.0] + [0.0] * 383, dtype=np.float32)
        chunk_vec = np.array([0.9] + [0.0] * 383, dtype=np.float32)
        
        mock_vector_index.chunk_ids_matching.return_value = [chunk_id]
        mock_vector_index.get_vector.return_value = chunk_vec
        mock_embedder.embed.return_value = query_vec
        
        # Complete chunk metadata
        mock_kb_store.get_chunks_by_ids.return_value = {
            chunk_id: {
                "chunk_id": chunk_id,
                "chunk_text": "Test content with all fields",
                "chunk_index": 5,
                "file_id": file_id,
                "filename": "document.pdf",
                "file_type": "application/pdf",
                "source_page": 10,
                "source_section": "Chapter 3"
            }
        }
        
        response = manager.search("query", top_k=5, file_id=file_id)
        
        assert response.success
        assert len(response.results) == 1
        
        result = response.results[0]
        
        # Verify all required fields
        assert result.chunk_id == chunk_id
        assert result.chunk_text == "Test content with all fields"
        assert result.chunk_index == 5
        assert abs(result.similarity - 0.9) < 0.01
        assert result.file_id == file_id
        assert result.filename == "document.pdf"
        assert result.file_type == "application/pdf"
        assert result.source_page == 10
        assert result.source_section == "Chapter 3"
    
    def test_get_vector_none_handling(self):
        """Test that get_vector returning None doesn't break search."""
        from nanobot.kb.index_manager import IndexManager
        
        mock_kb_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.dimension = 384
        
        manager = IndexManager(mock_kb_store, embedder=mock_embedder)
        mock_vector_index = MagicMock()
        manager._vector_index = mock_vector_index
        
        file_id = "test-file"
        chunks = [f"{file_id}_chunk_{i}" for i in range(1, 4)]
        
        query_vec = np.array([1.0] + [0.0] * 383, dtype=np.float32)
        valid_vec = np.array([0.8] + [0.0] * 383, dtype=np.float32)
        
        mock_vector_index.chunk_ids_matching.return_value = chunks
        
        # chunk_2 returns None (missing vector)
        def mock_get_vector(chunk_id):
            if chunk_id == chunks[0]:
                return valid_vec
            elif chunk_id == chunks[1]:
                return None  # Missing vector
            elif chunk_id == chunks[2]:
                return valid_vec
            return None
        
        mock_vector_index.get_vector.side_effect = mock_get_vector
        mock_embedder.embed.return_value = query_vec
        
        mock_kb_store.get_chunks_by_ids.return_value = {
            chunks[0]: {
                "chunk_id": chunks[0], "chunk_text": "First", "chunk_index": 1,
                "file_id": file_id, "filename": "test.txt", "file_type": "text/plain"
            },
            chunks[2]: {
                "chunk_id": chunks[2], "chunk_text": "Third", "chunk_index": 3,
                "file_id": file_id, "filename": "test.txt", "file_type": "text/plain"
            }
        }
        
        response = manager.search("query", top_k=5, file_id=file_id)
        
        # Should succeed with 2 valid chunks (chunk_2 skipped)
        assert response.success
        assert len(response.results) == 2
        assert response.results[0].chunk_text == "First"
        assert response.results[1].chunk_text == "Third"
    
    def test_empty_file_search(self):
        """Test search on file with no chunks returns empty results."""
        from nanobot.kb.index_manager import IndexManager
        
        mock_kb_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.dimension = 384
        
        manager = IndexManager(mock_kb_store, embedder=mock_embedder)
        mock_vector_index = MagicMock()
        manager._vector_index = mock_vector_index
        
        file_id = "empty-file"
        
        # No chunks for this file
        mock_vector_index.chunk_ids_matching.return_value = []
        
        query_vec = np.array([1.0] + [0.0] * 383, dtype=np.float32)
        mock_embedder.embed.return_value = query_vec
        
        response = manager.search("query", top_k=5, file_id=file_id)
        
        assert response.success
        assert len(response.results) == 0
        assert response.candidate_count == 0
        assert response.returned_count == 0


class TestKBRead:
    """Test kb_read functionality."""
    
    def test_read_single_part_file(self):
        """Test reading a file that fits in one part."""
        from nanobot.kb.store import KnowledgeBaseStore
        from unittest.mock import MagicMock
        
        mock_store = MagicMock()
        kb_store = KnowledgeBaseStore(mock_store)
        
        file_id = "test-file"
        full_text = "This is a short file that fits in one part."
        
        # Mock get_chunks to return full text at chunk_index=0
        mock_store.run_read.return_value = [
            {
                "chunk_id": f"{file_id}_chunk_0",
                "file_id": file_id,
                "chunk_text": full_text,
                "chunk_index": 0,
                "source_page": None,
                "source_section": None
            }
        ]
        
        chunks = kb_store.get_chunks(file_id, start_index=0)
        
        assert len(chunks) > 0
        assert chunks[0]["chunk_index"] == 0
        assert chunks[0]["chunk_text"] == full_text
    
    def test_read_multi_part_file(self):
        """Test reading a file split into multiple parts."""
        # Create text that requires 3 parts (10000 chars each)
        part1_text = "A" * 10000
        part2_text = "B" * 10000
        part3_text = "C" * 5000
        full_text = part1_text + part2_text + part3_text
        
        PART_SIZE = 10000
        total_parts = (len(full_text) + PART_SIZE - 1) // PART_SIZE
        
        assert total_parts == 3
        
        # Extract parts
        part0 = full_text[0:10000]
        part1 = full_text[10000:20000]
        part2 = full_text[20000:25000]
        
        assert len(part0) == 10000
        assert len(part1) == 10000
        assert len(part2) == 5000
        assert part0 == part1_text
        assert part1 == part2_text
        assert part2 == part3_text
    
    def test_part_index_calculation(self):
        """Test part_index boundary calculations."""
        # Test various file sizes
        test_cases = [
            (5000, 1),      # < 10000: 1 part
            (10000, 1),     # = 10000: 1 part
            (10001, 2),     # > 10000: 2 parts
            (20000, 2),     # = 20000: 2 parts
            (20001, 3),     # > 20000: 3 parts
            (25000, 3),     # 25000: 3 parts
        ]
        
        PART_SIZE = 10000
        for text_len, expected_parts in test_cases:
            actual_parts = (text_len + PART_SIZE - 1) // PART_SIZE
            assert actual_parts == expected_parts, f"Failed for text_len={text_len}"
    
    def test_part_index_out_of_range(self):
        """Test part_index validation."""
        full_text = "A" * 15000  # 2 parts
        PART_SIZE = 10000
        total_parts = (len(full_text) + PART_SIZE - 1) // PART_SIZE
        
        assert total_parts == 2
        
        # Valid indices: 0, 1
        # Invalid: 2 and above
        valid_indices = [0, 1]
        invalid_indices = [2, 3, 10]
        
        for idx in valid_indices:
            assert idx < total_parts
        
        for idx in invalid_indices:
            assert idx >= total_parts
    
    def test_empty_file(self):
        """Test reading empty file."""
        from nanobot.kb.store import KnowledgeBaseStore
        from unittest.mock import MagicMock
        
        mock_store = MagicMock()
        kb_store = KnowledgeBaseStore(mock_store)
        
        file_id = "empty-file"
        
        # Mock get_chunks to return empty text
        mock_store.run_read.return_value = [
            {
                "chunk_id": f"{file_id}_chunk_0",
                "file_id": file_id,
                "chunk_text": "",
                "chunk_index": 0,
                "source_page": None,
                "source_section": None
            }
        ]
        
        chunks = kb_store.get_chunks(file_id, start_index=0)
        
        assert len(chunks) > 0
        assert chunks[0]["chunk_text"] == ""
    
    def test_no_full_text_chunk(self):
        """Test file without chunk_index=0."""
        from nanobot.kb.store import KnowledgeBaseStore
        from unittest.mock import MagicMock
        
        mock_store = MagicMock()
        kb_store = KnowledgeBaseStore(mock_store)
        
        file_id = "test-file"
        
        # Mock get_chunks to return only chunk_index >= 1
        mock_store.run_read.return_value = [
            {
                "chunk_id": f"{file_id}_chunk_1",
                "file_id": file_id,
                "chunk_text": "Chunk 1",
                "chunk_index": 1,
                "source_page": None,
                "source_section": None
            }
        ]
        
        chunks = kb_store.get_chunks(file_id, start_index=0)
        
        # Find chunk_index=0
        full_text_chunk = None
        for chunk in chunks:
            if chunk.get("chunk_index") == 0:
                full_text_chunk = chunk
                break
        
        assert full_text_chunk is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
