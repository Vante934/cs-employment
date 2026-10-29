"""F05-A1: IDMap2 deletion, legacy migration, ID lifecycle and safe persistence.

Real faiss-cpu, real SQLite and real temp directories. Vectors are deterministic;
no embedding model is downloaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import faiss
import numpy as np
import pytest

import nanobot.kb.index as index_module
from nanobot.kb.chunker import chunk_file
from nanobot.kb.embedder import Embedder
from nanobot.kb.index import LEGACY_INDEX_FILE, MAPPING_FILE, VectorIndex
from nanobot.kb.index_manager import IndexManager
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.session.sqlite_store import SqliteSessionStore

DIM = 8
KEYWORDS = ["apple", "banana", "cherry", "durian", "elder", "fig", "grape", "honey"]


def unit(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


class KeywordModel:
    """Maps a text to the one-hot axis of the first keyword it contains."""

    dimension = DIM

    def encode(self, sentences, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]
        rows = []
        for text in sentences:
            idx = next((i for i, k in enumerate(KEYWORDS) if k in text.lower()), DIM - 1)
            rows.append(unit(idx))
        return np.vstack(rows)


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    return tmp_path / "index"


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


def write_legacy(directory: Path, vectors: list[np.ndarray], mapping: dict[str, int], next_id: int):
    """Write a pre-F05 index: plain IndexFlatIP + mapping.json without 'format'."""
    directory.mkdir(parents=True, exist_ok=True)
    flat = faiss.IndexFlatIP(DIM)
    flat.add(np.vstack(vectors).astype(np.float32))
    faiss.write_index(flat, str(directory / LEGACY_INDEX_FILE))
    (directory / MAPPING_FILE).write_text(json.dumps({
        "dimension": DIM, "chunk_to_faiss": mapping, "next_faiss_id": next_id,
    }))


# --- 1/2: IDMap2 add, search, real removal ---

def test_new_index_is_idmap2_and_remove_deletes_vector(index_dir):
    index = VectorIndex(DIM, index_dir)
    for i in range(3):
        index.add(f"c{i}", unit(i))
    assert isinstance(index._index, faiss.IndexIDMap2)

    assert index.search(unit(1), top_k=1)[0].chunk_id == "c1"
    assert index.remove("c1") is True

    assert index._index.ntotal == index.count() == 2
    stored = set(faiss.vector_to_array(index._index.id_map).tolist())
    assert stored == {index.get_faiss_id("c0"), index.get_faiss_id("c2")}
    hits = {r.chunk_id for r in index.search(unit(1), top_k=3)}
    assert hits == {"c0", "c2"}


def test_remove_then_reload_keeps_remaining_vectors(index_dir):
    index = VectorIndex(DIM, index_dir)
    for i in range(3):
        index.add(f"c{i}", unit(i))
    index.save()
    index.remove("c0")
    index.save()

    reloaded = VectorIndex(DIM, index_dir)
    assert reloaded.load() is True
    assert reloaded.count() == reloaded._index.ntotal == 2
    assert reloaded.search(unit(2), top_k=1)[0].chunk_id == "c2"
    assert not reloaded.has_chunk("c0")


@pytest.fixture
def kb_store(tmp_path: Path) -> KnowledgeBaseStore:
    store = SqliteSessionStore(tmp_path)
    store.initialize(tmp_path)
    return KnowledgeBaseStore(store)


def make_manager(kb_store: KnowledgeBaseStore, index_dir: Path) -> IndexManager:
    return IndexManager(kb_store, embedder=Embedder(model=KeywordModel()), index_dir=index_dir)


def add_indexed_file(kb_store, manager, name: str, text: str) -> str:
    file_id = kb_store.create_file_record(
        filename=name, file_type="txt", file_size=len(text),
        file_path=f"/fake/{name}", file_hash=f"hash-{name}", status="pending",
    )
    kb_store.save_parsed_result(file_id, text, None, len(text))
    assert chunk_file(file_id, kb_store, chunk_size=500, overlap=50).success
    assert manager.build_index_for_file(file_id).success
    return file_id


# --- 3: delete, then a fresh IndexManager (process restart) ---

def test_delete_file_then_restart_keeps_index(kb_store, index_dir):
    manager = make_manager(kb_store, index_dir)
    apple = add_indexed_file(kb_store, manager, "a.txt", "apple notes")
    cherry = add_indexed_file(kb_store, manager, "c.txt", "cherry notes")
    manager.delete_file_complete(apple)

    restarted = make_manager(kb_store, index_dir)
    index = restarted.vector_index
    assert index.count() == index._index.ntotal == 1

    response = restarted.search("cherry", top_k=5)
    assert response.success
    assert [r.file_id for r in response.results] == [cherry]

    response_apple = restarted.search("apple", top_k=5)
    assert response_apple.success
    assert all(r.file_id != apple for r in response_apple.results)


# --- 4/5: legacy IndexFlatIP migration and orphan handling ---

def test_legacy_index_migrates_with_identical_results(index_dir):
    vectors = [unit(0), unit(1), unit(2)]
    write_legacy(index_dir, vectors, {"c0": 0, "c1": 1, "c2": 2}, next_id=3)

    index = VectorIndex(DIM, index_dir)
    assert index.load() is True
    assert isinstance(index._index, faiss.IndexIDMap2)
    assert index.is_dirty()
    for i in range(3):
        top = index.search(unit(i), top_k=1)[0]
        assert top.chunk_id == f"c{i}"
        assert top.similarity == pytest.approx(1.0)

    index.save()
    assert not (index_dir / LEGACY_INDEX_FILE).exists()
    reloaded = VectorIndex(DIM, index_dir)
    reloaded.load()
    assert not reloaded.is_dirty()
    assert reloaded.search(unit(2), top_k=1)[0].chunk_id == "c2"


def test_legacy_orphan_vectors_are_dropped_not_remapped(index_dir):
    # Position 1 was removed under the old scheme: its vector is still in the
    # flat index but no chunk maps to it.
    write_legacy(index_dir, [unit(0), unit(1), unit(2)], {"c0": 0, "c2": 2}, next_id=3)

    index = VectorIndex(DIM, index_dir)
    index.load()
    assert index._index.ntotal == index.count() == 2
    assert index.get_faiss_id("c0") == 0
    assert index.get_faiss_id("c2") == 2
    # The orphan's direction must not surface under any chunk with similarity 1.
    assert all(r.similarity < 0.5 for r in index.search(unit(1), top_k=2))
    assert index.add("new", unit(3)) == 3


# --- 6: FAISS ID lifecycle ---

def test_clear_does_not_reuse_ids(index_dir):
    index = VectorIndex(DIM, index_dir)
    used = {index.add(f"c{i}", unit(i)) for i in range(3)}
    index.clear()
    assert index.add("after_clear", unit(4)) not in used


def test_ids_stay_monotonic_across_clear_save_and_reload(index_dir):
    index = VectorIndex(DIM, index_dir)
    used = {index.add(f"c{i}", unit(i)) for i in range(3)}
    index.clear()
    index.save()

    reloaded = VectorIndex(DIM, index_dir)
    reloaded.load()
    assert reloaded.add("fresh", unit(5)) not in used


def test_reload_avoids_ids_even_if_next_id_is_stale(index_dir):
    write_legacy(index_dir, [unit(0), unit(1)], {"c0": 0, "c1": 1}, next_id=0)
    index = VectorIndex(DIM, index_dir)
    index.load()
    assert index.add("fresh", unit(2)) == 2


def test_removing_highest_id_then_restart_does_not_reuse_it(index_dir):
    index = VectorIndex(DIM, index_dir)
    used = {index.add(f"c{i}", unit(i)) for i in range(3)}
    assert index.remove("c2") is True
    index.save()
    assert json.loads((index_dir / MAPPING_FILE).read_text())["next_faiss_id"] == 3

    reloaded = VectorIndex(DIM, index_dir)
    reloaded.load()
    new_id = reloaded.add("fresh", unit(5))
    assert new_id not in used
    assert new_id == 3


def test_legacy_orphan_at_highest_position_is_not_reused(index_dir):
    # Position 2 is an orphan and next_faiss_id is stale; the legacy ntotal
    # watermark is what keeps ID 2 retired.
    write_legacy(index_dir, [unit(0), unit(1), unit(2)], {"c0": 0, "c1": 1}, next_id=2)
    index = VectorIndex(DIM, index_dir)
    index.load()
    assert index.add("fresh", unit(3)) == 3
    index.save()

    reloaded = VectorIndex(DIM, index_dir)
    reloaded.load()
    assert reloaded.add("fresh2", unit(4)) == 4


# --- 7: save failure never replaces a good index with an empty one ---

def test_failed_index_write_keeps_previous_files(index_dir, monkeypatch):
    index = VectorIndex(DIM, index_dir)
    index.add("keep", unit(0))
    index.save()
    before = snapshot(index_dir)

    index.clear()

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    index.add("other", unit(1))
    monkeypatch.setattr(faiss, "write_index", boom)
    with pytest.raises(OSError):
        index.save()
    assert snapshot(index_dir) == before

    reloaded = VectorIndex(DIM, index_dir)
    reloaded.load()
    assert reloaded.has_chunk("keep") and not reloaded.has_chunk("other")


def test_failed_mapping_commit_keeps_previous_files(index_dir, monkeypatch):
    index = VectorIndex(DIM, index_dir)
    index.add("keep", unit(0))
    index.save()
    before = snapshot(index_dir)

    index.clear()
    monkeypatch.setattr(
        VectorIndex, "_atomic_write_json",
        staticmethod(lambda *_a, **_k: (_ for _ in ()).throw(OSError("replace failed"))),
    )
    with pytest.raises(OSError):
        index.save()
    assert snapshot(index_dir) == before
    assert index.is_dirty()


def test_mapping_replace_failure_then_restart_serves_old_data(index_dir, monkeypatch):
    index = VectorIndex(DIM, index_dir)
    index.add("keep", unit(0))
    index.save()
    old_index_file = json.loads((index_dir / MAPPING_FILE).read_text())["index_file"]
    before = snapshot(index_dir)

    index.add("other", unit(1))
    written: list[str] = []
    real_write = faiss.write_index

    def tracking_write(idx, path):
        written.append(Path(path).name)
        real_write(idx, path)

    def failing_replace(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(faiss, "write_index", tracking_write)
    monkeypatch.setattr(index_module.os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace failed"):
        index.save()
    monkeypatch.undo()

    assert len(written) == 1 and written[0] != old_index_file
    assert snapshot(index_dir) == before
    assert not list(index_dir.glob("*.tmp"))

    restarted = VectorIndex(DIM, index_dir)
    assert restarted.load() is True
    assert restarted.count() == 1
    assert not restarted.has_chunk("other")
    assert restarted.search(unit(0), top_k=1)[0].chunk_id == "keep"


def test_uncommitted_index_file_is_ignored_and_old_file_survives(index_dir):
    index = VectorIndex(DIM, index_dir)
    index.add("keep", unit(0))
    index.save()
    old_index_file = json.loads((index_dir / MAPPING_FILE).read_text())["index_file"]

    # Simulates a crash between writing the new index file and committing the
    # mapping: the stray file holds data no mapping refers to.
    stray = faiss.IndexIDMap2(faiss.IndexFlatIP(DIM))
    stray.add_with_ids(unit(1).reshape(1, -1), np.array([0], dtype=np.int64))
    stray_name = "faiss-0000uncommitted.index"
    faiss.write_index(stray, str(index_dir / stray_name))

    restarted = VectorIndex(DIM, index_dir)
    assert restarted.load() is True
    top = restarted.search(unit(1), top_k=1)[0]
    assert top.chunk_id == "keep" and top.similarity < 0.5
    assert (index_dir / old_index_file).exists()
    assert (index_dir / stray_name).exists()

    restarted.add("next", unit(2))
    restarted.save()
    new_index_file = json.loads((index_dir / MAPPING_FILE).read_text())["index_file"]
    remaining = {p.name for p in index_dir.glob("faiss-*.index")}
    assert remaining == {new_index_file}

    final = VectorIndex(DIM, index_dir)
    final.load()
    assert final.search(unit(0), top_k=1)[0].chunk_id == "keep"
    assert final.search(unit(2), top_k=1)[0].chunk_id == "next"


# --- 8: corrupted files and failed migration never reset silently ---

@pytest.mark.parametrize("payload", ["{not json", json.dumps([1, 2]),
                                     json.dumps({"dimension": DIM})])
def test_corrupted_mapping_raises_and_leaves_files(index_dir, payload):
    index_dir.mkdir(parents=True)
    (index_dir / MAPPING_FILE).write_text(payload)
    before = snapshot(index_dir)
    with pytest.raises(RuntimeError, match="Corrupted mapping"):
        VectorIndex(DIM, index_dir).load()
    assert snapshot(index_dir) == before


def test_legacy_mapping_beyond_index_size_fails(index_dir):
    write_legacy(index_dir, [unit(0)], {"c0": 0, "ghost": 5}, next_id=6)
    before = snapshot(index_dir)
    with pytest.raises(RuntimeError, match="beyond index size"):
        VectorIndex(DIM, index_dir).load()
    assert snapshot(index_dir) == before


def test_missing_referenced_index_file_fails(index_dir):
    index = VectorIndex(DIM, index_dir)
    index.add("c0", unit(0))
    index.save()
    for p in index_dir.glob("faiss-*.index"):
        p.unlink()
    with pytest.raises(RuntimeError, match="missing"):
        VectorIndex(DIM, index_dir).load()


def test_empty_legacy_index_migrates_orphan_vectors_as_empty(index_dir):
    write_legacy(index_dir, [unit(0), unit(1)], {}, next_id=2)
    index = VectorIndex(DIM, index_dir)
    assert index.load()
    assert index.count() == 0
    assert index.is_dirty()
    index.save()
    reloaded = VectorIndex(DIM, index_dir)
    assert reloaded.load()
    assert reloaded.count() == 0
    assert reloaded.add("first_valid", unit(2)) == 2


def test_empty_legacy_index_without_mapping_keeps_legacy_id_watermark(index_dir):
    write_legacy(index_dir, [unit(0), unit(1)], {}, next_id=0)
    index = VectorIndex(DIM, index_dir)
    assert index.load()
    assert index.add("new_chunk", unit(2)) == 2


def test_manager_raises_instead_of_resetting_on_corrupt_index(kb_store, index_dir):
    manager = make_manager(kb_store, index_dir)
    add_indexed_file(kb_store, manager, "a.txt", "apple notes")
    (index_dir / MAPPING_FILE).write_text("{broken")
    before = snapshot(index_dir)

    restarted = make_manager(kb_store, index_dir)
    with pytest.raises(RuntimeError, match="could not be loaded"):
        _ = restarted.vector_index
    assert snapshot(index_dir) == before
