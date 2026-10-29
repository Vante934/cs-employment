"""FAISS-based vector index for knowledge base semantic search.

This module manages the vector index lifecycle: building, persisting,
loading, and searching. It maintains a bidirectional mapping between
chunk_id (string) and FAISS internal integer IDs.
"""

from __future__ import annotations

import json
import os
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

MAPPING_FILE = "mapping.json"
# Pre-F05 indexes were a plain IndexFlatIP stored under this fixed name, with
# FAISS IDs equal to insertion positions.
LEGACY_INDEX_FILE = "faiss.index"
INDEX_FORMAT = "idmap2"


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

    def _new_index(self):
        """Build an empty ID-mapped inner-product index.

        IndexIDMap2 lets vectors carry explicit IDs and supports remove_ids, so
        deletions shrink ``ntotal`` instead of leaving orphaned vectors behind.
        """
        faiss = self._load_faiss()
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimension))

    def _create_index(self):
        """Create a new FAISS index."""
        self._index = self._new_index()

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
        self._index.add_with_ids(vector_2d, np.array([faiss_id], dtype=np.int64))

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
        """Remove a vector and its mapping from the index.

        Args:
            chunk_id: Chunk identifier

        Returns:
            True if removed, False if not found

        Raises:
            RuntimeError: If the mapping exists but FAISS has no such vector
        """
        faiss_id = self._chunk_to_faiss.get(chunk_id)
        if faiss_id is None:
            return False

        if self._index is not None:
            removed = self._index.remove_ids(np.array([faiss_id], dtype=np.int64))
            if removed != 1:
                raise RuntimeError(
                    f"FAISS vector {faiss_id} for chunk {chunk_id} is missing from the index"
                )

        del self._chunk_to_faiss[chunk_id]
        del self._faiss_to_chunk[faiss_id]
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
        """Persist index and mapping without ever exposing a half-written state.

        The FAISS data goes to a new uniquely named file first. ``mapping.json``
        names that file and is replaced atomically, so it is the single commit
        point: if anything fails before the replace, the previous mapping still
        points at the previous, intact index file.
        """
        index_file: str | None = None
        new_index_path: Path | None = None
        if self._index is not None and self._index.ntotal > 0:
            faiss = self._load_faiss()
            index_file = f"faiss-{uuid.uuid4().hex}.index"
            new_index_path = self.index_dir / index_file
            try:
                faiss.write_index(self._index, str(new_index_path))
                with open(new_index_path, "rb+") as f:
                    os.fsync(f.fileno())
            except Exception:
                new_index_path.unlink(missing_ok=True)
                raise

        mapping_data = {
            "format": INDEX_FORMAT,
            "dimension": self.dimension,
            "index_file": index_file,
            "chunk_to_faiss": self._chunk_to_faiss,
            "next_faiss_id": self._next_faiss_id,
        }
        try:
            self._atomic_write_json(self.index_dir / MAPPING_FILE, mapping_data)
        except Exception:
            if new_index_path is not None:
                new_index_path.unlink(missing_ok=True)
            raise

        self._dirty = False
        self._remove_stale_index_files(keep=index_file)

    @staticmethod
    def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _remove_stale_index_files(self, keep: str | None) -> None:
        """Delete index files no longer referenced by the committed mapping."""
        candidates = list(self.index_dir.glob("faiss-*.index"))
        legacy = self.index_dir / LEGACY_INDEX_FILE
        if legacy.exists():
            candidates.append(legacy)
        for path in candidates:
            if path.name == keep:
                continue
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Could not remove stale index file {}: {}", path, e)

    def load(self) -> bool:
        """Load index and mapping from disk, migrating the legacy format.

        Instance state is only replaced after the files validate, so a failed
        load leaves this object unchanged and never touches the files on disk.

        Returns:
            True if loaded, False if no committed mapping exists

        Raises:
            RuntimeError: If files are corrupted, inconsistent, or cannot be migrated
        """
        mapping_path = self.index_dir / MAPPING_FILE
        if not mapping_path.exists():
            if (self.index_dir / LEGACY_INDEX_FILE).exists():
                raise RuntimeError(
                    f"Found {LEGACY_INDEX_FILE} without {MAPPING_FILE}; "
                    "vectors cannot be mapped to chunks"
                )
            # faiss-*.index files without a mapping are uncommitted saves.
            return False

        try:
            mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Corrupted mapping file: {e}") from e

        try:
            chunk_to_faiss, next_faiss_id = self._parse_mapping(mapping_data)
            if mapping_data.get("format") == INDEX_FORMAT:
                index = self._load_idmap2(mapping_data.get("index_file"), chunk_to_faiss)
                migrated = False
                legacy_next_id = 0
            elif "format" not in mapping_data:
                index, legacy_next_id = self._migrate_legacy(chunk_to_faiss)
                migrated = True
            else:
                raise RuntimeError(f"Unknown index format: {mapping_data['format']!r}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to load index: {e}") from e

        highest_id = max(chunk_to_faiss.values(), default=-1)
        self._index = index
        self._chunk_to_faiss = chunk_to_faiss
        self._faiss_to_chunk = {v: k for k, v in chunk_to_faiss.items()}
        self._next_faiss_id = max(next_faiss_id, legacy_next_id, highest_id + 1)
        # A migrated index exists only in memory until the next save().
        self._dirty = migrated
        return True

    def _parse_mapping(self, data: Any) -> tuple[dict[str, int], int]:
        if not isinstance(data, dict):
            raise RuntimeError("Corrupted mapping file: top level is not an object")
        if data.get("dimension") != self.dimension:
            raise RuntimeError(
                f"Dimension mismatch: index={data.get('dimension')}, "
                f"expected={self.dimension}"
            )
        raw_mapping = data.get("chunk_to_faiss")
        next_faiss_id = data.get("next_faiss_id")
        if not isinstance(raw_mapping, dict) or not isinstance(next_faiss_id, int) or isinstance(next_faiss_id, bool) or next_faiss_id < 0:
            raise RuntimeError("Corrupted mapping file: invalid chunk_to_faiss or next_faiss_id")

        chunk_to_faiss: dict[str, int] = {}
        for chunk_id, faiss_id in raw_mapping.items():
            if not isinstance(faiss_id, int) or isinstance(faiss_id, bool) or faiss_id < 0:
                raise RuntimeError(f"Corrupted mapping file: invalid FAISS ID for {chunk_id!r}")
            chunk_to_faiss[chunk_id] = faiss_id
        if len(set(chunk_to_faiss.values())) != len(chunk_to_faiss):
            raise RuntimeError("Corrupted mapping file: duplicate FAISS IDs")
        return chunk_to_faiss, next_faiss_id

    def _load_idmap2(self, index_file: Any, chunk_to_faiss: dict[str, int]):
        if index_file is None:
            if chunk_to_faiss:
                raise RuntimeError("Mapping lists vectors but names no index file")
            return None
        if not isinstance(index_file, str) or Path(index_file).name != index_file:
            raise RuntimeError(f"Invalid index file name in mapping: {index_file!r}")

        index_path = self.index_dir / index_file
        if not index_path.exists():
            raise RuntimeError(f"Index file referenced by mapping is missing: {index_file}")

        faiss = self._load_faiss()
        index = faiss.read_index(str(index_path))
        if not isinstance(index, faiss.IndexIDMap2):
            raise RuntimeError(f"Expected IndexIDMap2, got {type(index).__name__}")
        if index.d != self.dimension:
            raise RuntimeError(f"Dimension mismatch: index={index.d}, expected={self.dimension}")

        stored_ids = set(faiss.vector_to_array(index.id_map).tolist())
        if stored_ids != set(chunk_to_faiss.values()):
            raise RuntimeError(
                f"Index and mapping disagree: FAISS has {len(stored_ids)} IDs, "
                f"mapping has {len(chunk_to_faiss)}"
            )
        return index

    def _migrate_legacy(self, chunk_to_faiss: dict[str, int]):
        """Convert a pre-F05 IndexFlatIP into IndexIDMap2.

        Legacy FAISS IDs are insertion positions, so each mapped vector is
        reconstructed from its position and re-added under the same ID. Positions
        with no mapping are vectors whose chunks were removed; they are dropped.
        """
        legacy_path = self.index_dir / LEGACY_INDEX_FILE
        if not legacy_path.exists():
            if not chunk_to_faiss:
                return None, 0
            raise RuntimeError(f"Legacy mapping lists vectors but {LEGACY_INDEX_FILE} is missing")

        faiss = self._load_faiss()
        legacy = faiss.read_index(str(legacy_path))
        if not isinstance(legacy, faiss.IndexFlat):
            raise RuntimeError(
                f"Legacy index must be a flat index, got {type(legacy).__name__}"
            )
        if legacy.d != self.dimension:
            raise RuntimeError(f"Dimension mismatch: index={legacy.d}, expected={self.dimension}")
        if not chunk_to_faiss:
            logger.info("Migrated empty legacy FAISS index; dropped {} orphaned vectors", legacy.ntotal)
            return None, legacy.ntotal
        out_of_range = [fid for fid in chunk_to_faiss.values() if fid >= legacy.ntotal]
        if out_of_range:
            raise RuntimeError(
                f"Legacy mapping references {len(out_of_range)} vectors beyond "
                f"index size {legacy.ntotal}"
            )

        ids = np.array(sorted(chunk_to_faiss.values()), dtype=np.int64)
        vectors = np.vstack([legacy.reconstruct(int(i)) for i in ids]).astype(np.float32)
        index = self._new_index()
        index.add_with_ids(vectors, ids)

        dropped = legacy.ntotal - len(ids)
        logger.info(
            "Migrated legacy FAISS index: kept {} vectors, dropped {} orphaned vectors",
            len(ids), dropped,
        )
        return index, legacy.ntotal

    def chunk_ids_matching(self, pattern: str) -> list[str]:
        """List chunk IDs matching the given regex pattern.

        Args:
            pattern: Regular expression pattern (e.g., r"^file123_chunk_\\d+$")

        Returns:
            List of matching chunk IDs
        """
        import re
        compiled = re.compile(pattern)
        return [cid for cid in self._chunk_to_faiss.keys() if compiled.match(cid)]

    def clear(self) -> None:
        """Clear all vectors and mappings.

        ``_next_faiss_id`` is kept so IDs recorded in SQLite before the clear are
        never handed out again to different chunks.
        """
        self._index = None
        self._chunk_to_faiss.clear()
        self._faiss_to_chunk.clear()
        self._dirty = True
