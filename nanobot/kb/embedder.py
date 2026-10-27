"""Text embedding for knowledge base using sentence-transformers.

This module provides a unified interface for converting text chunks into
dense vector representations suitable for semantic search.
"""

from __future__ import annotations

import numpy as np
from typing import Protocol


# Model configuration - centralized for easy swapping
DEFAULT_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Dimensions: 384
# Languages: 50+ including English and Chinese
# Size: ~420MB on first download


class EmbeddingModel(Protocol):
    """Protocol for embedding model interface (for testing/mocking)."""
    
    def encode(self, sentences: str | list[str], **kwargs) -> np.ndarray:
        """Encode text(s) to embeddings."""
        ...


class Embedder:
    """Text embedding wrapper using sentence-transformers."""
    
    def __init__(self, model_name: str = DEFAULT_MODEL_NAME, model: EmbeddingModel | None = None):
        """Initialize embedder.
        
        Args:
            model_name: Sentence-transformers model name
            model: Optional pre-loaded model (for testing/mocking)
        """
        self.model_name = model_name
        self._model = model
        self._dimension: int | None = None
    
    def _load_model(self) -> EmbeddingModel:
        """Lazy load the model on first use."""
        if self._model is not None:
            return self._model
        
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            ) from e
        
        try:
            self._model = SentenceTransformer(self.model_name)
            return self._model
        except Exception as e:
            raise RuntimeError(
                f"Failed to load embedding model '{self.model_name}'. "
                f"First use requires ~420MB download. Error: {e}"
            ) from e
    
    @property
    def dimension(self) -> int:
        """Get embedding dimension (lazy)."""
        if self._dimension is None:
            model = self._load_model()
            if hasattr(model, "get_sentence_embedding_dimension"):
                self._dimension = model.get_sentence_embedding_dimension()
            elif hasattr(model, "dimension"):
                self._dimension = int(model.dimension)
            else:
                # Get dimension from a test encoding
                test_vec = model.encode("test", normalize_embeddings=True)
                self._dimension = test_vec.shape[-1] if test_vec.ndim == 1 else test_vec.shape[1]
        return self._dimension
    
    def embed(self, text: str, normalize: bool = True) -> np.ndarray:
        """Embed a single text.
        
        Args:
            text: Input text
            normalize: Whether to L2-normalize the embedding (for cosine similarity)
        
        Returns:
            1D numpy array of shape (dimension,)
        
        Raises:
            ValueError: If text is empty or result is invalid
            RuntimeError: If model loading fails
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")
        
        model = self._load_model()
        
        try:
            embedding = model.encode(
                text,
                normalize_embeddings=normalize,
                convert_to_numpy=True
            )
        except Exception as e:
            raise RuntimeError(f"Embedding failed for text (len={len(text)}): {e}") from e
        
        # Validate output
        if not isinstance(embedding, np.ndarray):
            raise RuntimeError(f"Expected numpy array, got {type(embedding)}")
        
        # Handle both 1D and 2D outputs from different model implementations
        if embedding.ndim == 2:
            if embedding.shape[0] != 1:
                raise RuntimeError(f"Expected single embedding, got batch of {embedding.shape[0]}")
            embedding = embedding[0]
        
        if embedding.ndim != 1:
            raise RuntimeError(f"Expected 1D array, got shape {embedding.shape}")
        
        if not np.all(np.isfinite(embedding)):
            raise RuntimeError("Embedding contains NaN or Inf values")
        
        return embedding
    
    def embed_batch(
        self,
        texts: list[str],
        normalize: bool = True,
        batch_size: int = 32
    ) -> list[np.ndarray]:
        """Embed a batch of texts.
        
        Args:
            texts: List of input texts
            normalize: Whether to L2-normalize the embeddings
            batch_size: Batch size for processing
        
        Returns:
            List of 1D numpy arrays
        
        Raises:
            ValueError: If any text is empty
            RuntimeError: If model loading or encoding fails
        """
        if not texts:
            return []
        
        # Validate all texts
        for i, text in enumerate(texts):
            if not text or not text.strip():
                raise ValueError(f"Cannot embed empty text at index {i}")
        
        model = self._load_model()
        
        try:
            embeddings = model.encode(
                texts,
                normalize_embeddings=normalize,
                convert_to_numpy=True,
                batch_size=batch_size,
                show_progress_bar=False
            )
        except Exception as e:
            raise RuntimeError(f"Batch embedding failed for {len(texts)} texts: {e}") from e
        
        # Validate output
        if not isinstance(embeddings, np.ndarray):
            raise RuntimeError(f"Expected numpy array, got {type(embeddings)}")
        
        # Handle both 1D (single text) and 2D (batch) outputs
        if embeddings.ndim == 1:
            embeddings = embeddings.reshape(1, -1)
        
        if embeddings.ndim != 2:
            raise RuntimeError(f"Expected 1D or 2D array, got shape {embeddings.shape}")
        
        if embeddings.shape[0] != len(texts):
            raise RuntimeError(
                f"Expected {len(texts)} embeddings, got {embeddings.shape[0]}"
            )
        
        if not np.all(np.isfinite(embeddings)):
            raise RuntimeError("Embeddings contain NaN or Inf values")
        
        # Convert to list of 1D arrays
        return [embeddings[i] for i in range(len(texts))]


def get_embedder(model_name: str = DEFAULT_MODEL_NAME) -> Embedder:
    """Get embedder instance (factory function for easy testing)."""
    return Embedder(model_name=model_name)
