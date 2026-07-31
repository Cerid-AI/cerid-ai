"""Every ``_CacheBackend`` double must agree with chromadb 1.x.

Three hand-rolled clones of one protocol had diverged to the point of
contradicting each other on identical input: one treated ``delete(where={})``
as clear-all (chromadb **0.5** semantics), one raised, one silently did nothing.
The clear-all clone let the production clear-all path throw on every mutation
while the suite stayed green — the semantic cache was never actually cleared
(2026-07-29 audit).

Fake duplication is the drift engine, so this pins the behaviours that matter
across *all* doubles, and guards against a fourth clone appearing.
"""

import ast
import pathlib

import pytest

from tests.helpers.fake_chroma import FakeChromaBackend

_TESTS_DIR = pathlib.Path(__file__).resolve().parent


def _backend() -> FakeChromaBackend:
    b = FakeChromaBackend()
    b.upsert(ids=["a", "b"], embeddings=[[1.0, 0.0], [0.0, 1.0]])
    return b


def test_empty_where_raises_like_chromadb_1x():
    """The exact divergence that concealed a production defect."""
    with pytest.raises(ValueError, match="exactly one operator"):
        _backend().delete(where={})


def test_delete_by_ids_actually_removes():
    """A no-op delete makes orphan-eviction assertions vacuous."""
    b = _backend()
    b.delete(ids=["a"])
    assert b.get()["ids"] == ["b"]
    assert b.count() == 1


def test_get_exposes_ids_for_clear_all_path():
    """Production clears by fetching ids then deleting them."""
    b = _backend()
    b.delete(ids=b.get()["ids"])
    assert b.count() == 0


def test_non_empty_where_still_clears():
    b = _backend()
    b.delete(where={"tenant_id": "x"})
    assert b.count() == 0


def test_no_new_fake_backend_clones():
    """New ``_CacheBackend`` doubles belong in tests/helpers/, not inline.

    Inline clones drift silently; the canonical double is version-annotated
    against the pinned chromadb and reviewed as one thing.
    """
    allowed = {
        # Pre-existing clones, corrected in the 2026-07-29 audit. They may be
        # migrated to the canonical helper, but must not multiply.
        "test_semantic_cache.py",
        "test_e1_r16_cache_memory_scope.py",
        "test_e1_semantic_cache_consumer_scope.py",
    }
    offenders: list[str] = []
    for path in _TESTS_DIR.rglob("test_*.py"):
        if path.name in allowed:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - unparseable test file
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and "FakeBackend" in node.name:
                offenders.append(f"{path.relative_to(_TESTS_DIR)}::{node.name}")

    assert not offenders, (
        "new inline _CacheBackend double(s) found: "
        f"{offenders}. Use tests.helpers.fake_chroma.FakeChromaBackend so "
        "behaviour stays pinned to the shipped chromadb version."
    )
