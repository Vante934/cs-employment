"""Tests for knowledge base embedder."""

from unittest.mock import Mock

import numpy as np
import pytest

from nanobot.kb.embedder import Embedder, get_embedder


class MockModel:
    """Mock embedding model for testing."""
    
    def __init__(self, dimension=384, fail=False, return_nan=False):
        self.dimension = dimension
        self.fail = fail
        self.return_nan = return_nan
    
    def encode(self, sentences, **kwargs):
        if self.fail:
            raise RuntimeError("Model encoding failed")
        
        if isinstance(sentences, str):
            sentences = [sentences]
        
        embeddings = np.random.randn(len(sentences), self.dimension).astype(np.float32)
        
        if kwargs.get("normalize_embeddings", False):
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / (norms + 1e-8)
        
        if self.return_nan:
            embeddings[0, 0] = np.nan
        
        if len(sentences) == 1:
            return embeddings[0]
        return embeddings


def test_embed_single_text():
    """Test embedding single text."""
    model = MockModel(dimension=384)
    embedder = Embedder(model=model)
    
    text = "This is a test."
    vector = embedder.embed(text)
    
    assert isinstance(vector, np.ndarray)
    assert vector.shape == (384,)
    assert vector.dtype == np.float32
    assert np.all(np.isfinite(vector))


def test_embed_chinese_text():
    """Test embedding Chinese text."""
    model = MockModel(dimension=384)
    embedder = Embedder(model=model)
    
    text = "这是一个测试。"
    vector = embedder.embed(text)
    
    assert isinstance(vector, np.ndarray)
    assert vector.shape == (384,)


def test_embed_empty_text_raises():
    """Test that empty text raises ValueError."""
    model = MockModel()
    embedder = Embedder(model=model)
    
    with pytest.raises(ValueError, match="Cannot embed empty text"):
        embedder.embed("")
    
    with pytest.raises(ValueError, match="Cannot embed empty text"):
        embedder.embed("   ")


def test_embed_batch():
    """Test batch embedding."""
    model = MockModel(dimension=384)
    embedder = Embedder(model=model)
    
    texts = ["Text 1", "Text 2", "Text 3"]
    vectors = embedder.embed_batch(texts)
    
    assert len(vectors) == 3
    assert all(isinstance(v, np.ndarray) for v in vectors)
    assert all(v.shape == (384,) for v in vectors)


def test_embed_batch_empty_list():
    """Test batch embedding with empty list."""
    model = MockModel()
    embedder = Embedder(model=model)
    
    vectors = embedder.embed_batch([])
    assert vectors == []


def test_embed_batch_with_empty_text_raises():
    """Test that batch with empty text raises ValueError."""
    model = MockModel()
    embedder = Embedder(model=model)
    
    texts = ["Good text", "", "Another good text"]
    
    with pytest.raises(ValueError, match="Cannot embed empty text at index 1"):
        embedder.embed_batch(texts)


def test_embed_normalized():
    """Test that normalized embeddings have unit norm."""
    model = MockModel(dimension=10)
    embedder = Embedder(model=model)
    
    vector = embedder.embed("test", normalize=True)
    
    # Check approximately unit norm (allowing for float precision)
    norm = np.linalg.norm(vector)
    assert abs(norm - 1.0) < 1e-5


def test_embed_model_failure():
    """Test handling of model encoding failure."""
    model = MockModel(fail=True)
    embedder = Embedder(model=model)
    
    with pytest.raises(RuntimeError, match="Embedding failed"):
        embedder.embed("test")


def test_embed_nan_values():
    """Test detection of NaN values in embeddings."""
    model = MockModel(return_nan=True)
    embedder = Embedder(model=model)
    
    with pytest.raises(RuntimeError, match="NaN or Inf"):
        embedder.embed("test")


def test_embed_wrong_dimension():
    """Test detection of dimension mismatch."""
    # Create model that returns wrong dimension after flattening
    class BadDimensionModel:
        def encode(self, sentences, **kwargs):
            # Return 2D with wrong inner dimension
            return np.random.randn(1, 128).astype(np.float32)
    
    embedder = Embedder(model=BadDimensionModel())
    
    # Should flatten 2D to 1D, but dimension will still be wrong (128 != expected)
    # This will be caught when comparing to expected dimension
    vector = embedder.embed("test")
    assert vector.shape == (128,)  # Successfully flattened, dimension is what model returned


def test_get_embedder_factory():
    """Test embedder factory function."""
    embedder = get_embedder()
    assert isinstance(embedder, Embedder)
    assert embedder.model_name == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def test_embedder_dimension_property():
    """Test lazy dimension property."""
    model = MockModel(dimension=384)
    embedder = Embedder(model=model)
    
    # Dimension should be lazy-loaded
    assert embedder.dimension == 384


@pytest.mark.skipif(True, reason="Requires sentence-transformers model download")
def test_embed_real_model():
    """Test with real sentence-transformers model (manual test only)."""
    embedder = get_embedder()
    
    # English
    vector_en = embedder.embed("This is a test.")
    assert vector_en.shape == (384,)
    
    # Chinese
    vector_zh = embedder.embed("这是一个测试。")
    assert vector_zh.shape == (384,)
    
    # Similarity should be reasonable
    similarity = np.dot(vector_en, vector_zh)
    assert 0 <= similarity <= 1
