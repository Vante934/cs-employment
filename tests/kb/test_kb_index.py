"""Tests for knowledge base vector index."""

from pathlib import Path

import numpy as np
import pytest

from nanobot.kb.index import VectorIndex


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    """Create temporary index directory."""
    return tmp_path / "index"


def test_create_empty_index(index_dir):
    """Test creating an empty index."""
    index = VectorIndex(dimension=384, index_dir=index_dir)
    
    assert index.count() == 0
    assert index.dimension == 384


def test_add_single_vector(index_dir):
    """Test adding a single vector."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    vector = np.random.randn(10).astype(np.float32)
    vector = vector / np.linalg.norm(vector)  # Normalize
    
    faiss_id = index.add("chunk_1", vector)
    
    assert faiss_id == 0
    assert index.count() == 1
    assert index.has_chunk("chunk_1")


def test_add_multiple_vectors(index_dir):
    """Test adding multiple vectors."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    vectors = [np.random.randn(10).astype(np.float32) for _ in range(5)]
    vectors = [v / np.linalg.norm(v) for v in vectors]
    
    chunk_ids = [f"chunk_{i}" for i in range(5)]
    faiss_ids = index.add_batch(chunk_ids, vectors)
    
    assert len(faiss_ids) == 5
    assert index.count() == 5
    assert all(index.has_chunk(cid) for cid in chunk_ids)


def test_add_duplicate_chunk_id_raises(index_dir):
    """Test that adding duplicate chunk_id raises error."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    vector = np.random.randn(10).astype(np.float32)
    vector = vector / np.linalg.norm(vector)
    
    index.add("chunk_1", vector)
    
    with pytest.raises(ValueError, match="already in index"):
        index.add("chunk_1", vector)


def test_add_wrong_dimension_raises(index_dir):
    """Test that wrong dimension raises error."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    vector = np.random.randn(20).astype(np.float32)  # Wrong dimension
    
    with pytest.raises(ValueError, match="Vector shape mismatch"):
        index.add("chunk_1", vector)


def test_add_nan_vector_raises(index_dir):
    """Test that NaN vector raises error."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    vector = np.random.randn(10).astype(np.float32)
    vector[0] = np.nan
    
    with pytest.raises(ValueError, match="NaN or Inf"):
        index.add("chunk_1", vector)


def test_search_empty_index(index_dir):
    """Test searching in empty index."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    query = np.random.randn(10).astype(np.float32)
    query = query / np.linalg.norm(query)
    
    results = index.search(query, top_k=5)
    
    assert results == []


def test_search_similar_vectors(index_dir):
    """Test searching for similar vectors."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    # Add base vector
    base_vector = np.random.randn(10).astype(np.float32)
    base_vector = base_vector / np.linalg.norm(base_vector)
    index.add("chunk_1", base_vector)
    
    # Add similar vector (small perturbation)
    similar_vector = base_vector + np.random.randn(10).astype(np.float32) * 0.1
    similar_vector = similar_vector / np.linalg.norm(similar_vector)
    index.add("chunk_2", similar_vector)
    
    # Add dissimilar vector
    dissimilar_vector = -base_vector
    index.add("chunk_3", dissimilar_vector)
    
    # Search with base vector
    results = index.search(base_vector, top_k=3)
    
    assert len(results) == 3
    assert results[0].chunk_id == "chunk_1"  # Exact match should be first
    assert results[0].similarity > results[2].similarity  # Better than dissimilar


def test_search_top_k_limit(index_dir):
    """Test that top_k is respected."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    # Add 10 vectors
    for i in range(10):
        vector = np.random.randn(10).astype(np.float32)
        vector = vector / np.linalg.norm(vector)
        index.add(f"chunk_{i}", vector)
    
    query = np.random.randn(10).astype(np.float32)
    query = query / np.linalg.norm(query)
    
    results = index.search(query, top_k=5)
    
    assert len(results) == 5


def test_search_top_k_exceeds_total(index_dir):
    """Test that top_k is clamped to available vectors."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    # Add only 3 vectors
    for i in range(3):
        vector = np.random.randn(10).astype(np.float32)
        vector = vector / np.linalg.norm(vector)
        index.add(f"chunk_{i}", vector)
    
    query = np.random.randn(10).astype(np.float32)
    query = query / np.linalg.norm(query)
    
    results = index.search(query, top_k=10)
    
    assert len(results) == 3  # Only 3 available


def test_remove_vector(index_dir):
    """Test removing a vector."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    vector = np.random.randn(10).astype(np.float32)
    vector = vector / np.linalg.norm(vector)
    index.add("chunk_1", vector)
    
    assert index.has_chunk("chunk_1")
    assert index.count() == 1
    
    removed = index.remove("chunk_1")
    
    assert removed is True
    assert not index.has_chunk("chunk_1")
    assert index.count() == 0


def test_remove_nonexistent_chunk(index_dir):
    """Test removing non-existent chunk."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    removed = index.remove("nonexistent")
    assert removed is False


def test_save_and_load_empty_index(index_dir):
    """Test saving and loading empty index."""
    index = VectorIndex(dimension=384, index_dir=index_dir)
    
    index.save()
    
    # Load in new instance
    index2 = VectorIndex(dimension=384, index_dir=index_dir)
    loaded = index2.load()
    
    assert loaded is True
    assert index2.count() == 0


def test_save_and_load_with_vectors(index_dir):
    """Test saving and loading index with vectors."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    # Add vectors
    vectors = []
    for i in range(5):
        vector = np.random.randn(10).astype(np.float32)
        vector = vector / np.linalg.norm(vector)
        vectors.append(vector)
        index.add(f"chunk_{i}", vector)
    
    # Save
    index.save()
    
    # Load in new instance
    index2 = VectorIndex(dimension=10, index_dir=index_dir)
    loaded = index2.load()
    
    assert loaded is True
    assert index2.count() == 5
    assert all(index2.has_chunk(f"chunk_{i}") for i in range(5))
    
    # Verify search works after loading
    query = vectors[0]  # Use first vector as query
    results = index2.search(query, top_k=1)
    
    assert len(results) > 0
    assert results[0].chunk_id == "chunk_0"


def test_load_nonexistent_index(index_dir):
    """Test loading when index doesn't exist."""
    index = VectorIndex(dimension=384, index_dir=index_dir)
    
    loaded = index.load()
    assert loaded is False


def test_load_dimension_mismatch(index_dir):
    """Test loading with dimension mismatch."""
    index = VectorIndex(dimension=384, index_dir=index_dir)
    index.save()
    
    # Try to load with different dimension
    index2 = VectorIndex(dimension=512, index_dir=index_dir)
    
    with pytest.raises(RuntimeError, match="Dimension mismatch"):
        index2.load()


def test_clear_index(index_dir):
    """Test clearing index."""
    index = VectorIndex(dimension=10, index_dir=index_dir)
    
    # Add vectors
    for i in range(5):
        vector = np.random.randn(10).astype(np.float32)
        vector = vector / np.linalg.norm(vector)
        index.add(f"chunk_{i}", vector)
    
    assert index.count() == 5
    
    index.clear()
    
    assert index.count() == 0
    assert not index.has_chunk("chunk_0")
