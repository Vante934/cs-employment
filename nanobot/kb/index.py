"""FAISS-based vector index for knowledge base semantic search.

This module manages the vector index lifecycle: building, persisting,
loading, and searching. It maintains a bidirectional mapping between
chunk_id (string) and FAISS internal integer IDs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class SearchResult:
    """Single search result."""
    chunk_id: str
    similarity: float  # Cosine similarity in [0, 1] range
    distance: float    # Raw distance from FAISS


class VectorIndex:
    """FAISS vector index with chunk_id mapping."""
    
    def __init__(self, dimension: int, index_dir: Path):
        """Initialize vector index.
        
        Args:
            dimension: Embedding dimension
            index_dir: Directory for index files
        """
        self.dimension = dimension
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        
        self._index = None  # FAISS index (lazy loaded)
        self._chunk_to_faiss: dict[str, int] = {}  # chunk_id -> FAISS ID
        self._faiss_to_chunk: dict[int, str] = {}  # FAISS ID -> chunk_id
        self._next_faiss_id = 0
        self._dirty = False
    
    def _load_faiss(self):
        """Lazy load FAISS library."""
        try:
            import faiss
            return faiss
        except ImportError as e:
            raise RuntimeError(
                "faiss-cpu not installed. Install with: pip install faiss-cpu"
            ) from e
    
    def _create_index(self):
        """Create a new FAISS index."""
        faiss = self._load_faiss()
        # Use IndexFlatIP for normalized vectors (inner product = cosine similarity)
        self._index = faiss.IndexFlatIP(self.dimension)
    
    def add(self, chunk_id: str, vector: np.ndarray) -> int:
        """Add a single vector to the index.
        
        Args:
            chunk_id: Chunk identifier
            vector: Embedding vector (must be normalized)
        
        Returns:
            FAISS internal ID
        
        Raises:
            ValueError: If chunk_id already exists or vector is invalid
        """
        if chunk_id in self._chunk_to_faiss:
            raise ValueError(f"chunk_id already in index: {chunk_id}")
        
        if vector.shape != (self.dimension,):
            raise ValueError(
                f"Vector shape mismatch: expected ({self.dimension},), got {vector.shape}"
            )
        
        if not np.all(np.isfinite(vector)):
            raise ValueError("Vector contains NaN or Inf")
        
        if self._index is None:
            self._create_index()
        
        # FAISS expects 2D array
        vector_2d = vector.reshape(1, -1).astype(np.float32)
        
        faiss_id = self._next_faiss_id
        self._index.add(vector_2d)
        
        # Update mappings
        self._chunk_to_faiss[chunk_id] = faiss_id
        self._faiss_to_chunk[faiss_id] = chunk_id
        self._next_faiss_id += 1
        self._dirty = True
        
        return faiss_id
    
    def add_batch(self, chunk_ids: list[str], vectors: list[np.ndarray]) -> list[int]:
        """Add multiple vectors to the index.
        
        Args:
            chunk_ids: List of chunk identifiers
            vectors: List of embedding vectors
        
        Returns:
            List of FAISS internal IDs
        """
        if len(chunk_ids) != len(vectors):
            raise ValueError("chunk_ids and vectors must have same length")
        
        faiss_ids = []
        for chunk_id, vector in zip(chunk_ids, vectors):
            faiss_id = self.add(chunk_id, vector)
            faiss_ids.append(faiss_id)
        
        return faiss_ids
    
    def search(self, query_vector: np.ndarray, top_k: int = 5) -> list[SearchResult]:
        """Search for similar vectors.
        
        Args:
            query_vector: Query embedding (must be normalized)
            top_k: Number of results to return
        
        Returns:
            List of SearchResult, sorted by similarity (descending)
        """
        if self._index is None or self._index.ntotal == 0:
            return []
        
        if query_vector.shape != (self.dimension,):
            raise ValueError(
                f"Query vector shape mismatch: expected ({self.dimension},), got {query_vector.shape}"
            )
        
        # Clamp top_k to available vectors
        k = min(top_k, self._index.ntotal)
        
        # FAISS expects 2D array
        query_2d = query_vector.reshape(1, -1).astype(np.float32)
        
        # Search (returns distances and indices)
        distances, indices = self._index.search(query_2d, k)
        
        # Convert to SearchResult
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:  # FAISS returns -1 for missing results
                continue
            
            chunk_id = self._faiss_to_chunk.get(int(idx))
            if chunk_id is None:
                # Mapping corruption detected
                import warnings
                warnings.warn(
                    f"FAISS returned vector ID {idx} but mapping not found. "
                    f"Index may be corrupted. Consider rebuilding with rebuild_all(force=True)."
                )
                continue  # Skip corrupted entry
            
            # For normalized vectors with IndexFlatIP, distance = inner product = cosine similarity
            # Clamp to [0, 1] range (numerical errors may cause slight overflow)
            similarity = float(np.clip(dist, 0.0, 1.0))
            
            results.append(SearchResult(
                chunk_id=chunk_id,
                similarity=similarity,
                distance=float(dist)
            ))
        
        return results
    
    def remove(self, chunk_id: str) -> bool:
        """Remove a vector from the index.
        
        Note: FAISS doesn't support efficient removal. This marks the ID as deleted
        but doesn't free space. For clean removal, rebuild the index.
        
        Args:
            chunk_id: Chunk identifier
        
        Returns:
            True if removed, False if not found
        """
        faiss_id = self._chunk_to_faiss.get(chunk_id)
        if faiss_id is None:
            return False
        
        # Remove from mappings
        del self._chunk_to_faiss[chunk_id]
        del self._faiss_to_chunk[faiss_id]
        
        # Note: FAISS index still contains the vector, but it won't be returned
        # in search results due to missing mapping
        self._dirty = True
        return True
    
    def has_chunk(self, chunk_id: str) -> bool:
        """Check if chunk is in the index."""
        return chunk_id in self._chunk_to_faiss
    
    def get_faiss_id(self, chunk_id: str) -> int | None:
        """Get the persisted-index identifier for a chunk."""
        return self._chunk_to_faiss.get(chunk_id)
    
    def get_vector(self, chunk_id: str) -> np.ndarray | None:
        """Get a copy of a stored vector for rollback operations."""
        faiss_id = self.get_faiss_id(chunk_id)
        if faiss_id is None or self._index is None:
            return None
        return np.asarray(self._index.reconstruct(faiss_id), dtype=np.float32).copy()
    
    def count(self) -> int:
        """Get number of indexed chunks."""
        return len(self._chunk_to_faiss)
    
    def is_dirty(self) -> bool:
        """Return whether in-memory changes are not persisted."""
        return self._dirty
    
    def save(self) -> None:
        """Save index and mappings to disk."""
        if self._index is None:
            # Empty index - save empty mapping only
            mapping_path = self.index_dir / "mapping.json"
            mapping_path.write_text(json.dumps({
                "dimension": self.dimension,
                "chunk_to_faiss": {},
                "next_faiss_id": 0
            }))
            self._dirty = False
            return
        
        faiss = self._load_faiss()
        
        # Save FAISS index
        index_path = self.index_dir / "faiss.index"
        faiss.write_index(self._index, str(index_path))
        
        # Save mappings
        mapping_path = self.index_dir / "mapping.json"
        mapping_data = {
            "dimension": self.dimension,
            "chunk_to_faiss": self._chunk_to_faiss,
            "next_faiss_id": self._next_faiss_id
        }
        mapping_path.write_text(json.dumps(mapping_data, indent=2))
        self._dirty = False
    
    def load(self) -> bool:
        """Load index and mappings from disk.
        
        Returns:
            True if loaded successfully, False if files don't exist
        
        Raises:
            RuntimeError: If files are corrupted
        """
        index_path = self.index_dir / "faiss.index"
        mapping_path = self.index_dir / "mapping.json"
        
        # Check if files exist
        if not mapping_path.exists():
            return False
        
        try:
            # Load mappings
            mapping_data = json.loads(mapping_path.read_text())
            
            # Validate dimension
            if mapping_data["dimension"] != self.dimension:
                raise RuntimeError(
                    f"Dimension mismatch: index={mapping_data['dimension']}, "
                    f"expected={self.dimension}"
                )
            
            self._chunk_to_faiss = {k: int(v) for k, v in mapping_data["chunk_to_faiss"].items()}
            self._faiss_to_chunk = {int(v): k for k, v in self._chunk_to_faiss.items()}
            self._next_faiss_id = mapping_data["next_faiss_id"]
            self._dirty = False
            
            # Load FAISS index if it exists
            if index_path.exists() and self._chunk_to_faiss:
                faiss = self._load_faiss()
                self._index = faiss.read_index(str(index_path))
                
                # Validate index size matches mapping
                if self._index.ntotal != len(self._chunk_to_faiss):
                    raise RuntimeError(
                        f"Index size mismatch: FAISS={self._index.ntotal}, "
                        f"mapping={len(self._chunk_to_faiss)}"
                    )
            
            return True
            
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Corrupted mapping file: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to load index: {e}") from e
    
    def clear(self) -> None:
        """Clear all vectors and mappings."""
        self._index = None
        self._chunk_to_faiss.clear()
        self._faiss_to_chunk.clear()
        self._next_faiss_id = 0
        self._dirty = True
