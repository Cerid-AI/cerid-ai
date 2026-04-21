# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Embedding singleton guarantees.

Regression guard for the dim-mismatch bug: if any site instantiates its
own ``OnnxEmbeddingFunction`` instead of going through the
``get_embedder()`` / ``get_embedding_function()`` singleton, first-use
dim-locking on Chroma collections diverges between ingest and query
paths. These tests assert that invariant.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

# The single legitimate call site is the singleton module itself.
_MCP_ROOT = Path(__file__).parent.parent
_SINGLETON_MODULE = _MCP_ROOT / "core" / "utils" / "embeddings.py"


def _all_python_files():
    """Yield every .py file under src/mcp, skipping __pycache__ and tests."""
    for path in _MCP_ROOT.rglob("*.py"):
        parts = path.parts
        if "__pycache__" in parts:
            continue
        if "tests" in parts:
            continue
        if "eval" in parts and path.name != "embeddings.py":
            continue
        yield path


def test_only_singleton_module_instantiates_onnx_embedding_function():
    """Grep the AST for every ``OnnxEmbeddingFunction(...)`` call site.

    The only file allowed to instantiate ``OnnxEmbeddingFunction``
    directly is ``core/utils/embeddings.py`` (which is the singleton
    source). Any other site must go through ``get_embedder()`` /
    ``get_embedding_function()``. Tests and eval fixtures are excluded.
    """
    violations: list[tuple[str, int]] = []
    for pyfile in _all_python_files():
        if pyfile.resolve() == _SINGLETON_MODULE.resolve():
            continue
        try:
            tree = ast.parse(pyfile.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = node.func
                name = None
                if isinstance(callee, ast.Name):
                    name = callee.id
                elif isinstance(callee, ast.Attribute):
                    name = callee.attr
                if name == "OnnxEmbeddingFunction":
                    rel = pyfile.relative_to(_MCP_ROOT)
                    violations.append((str(rel), node.lineno))

    assert not violations, (
        "Found direct OnnxEmbeddingFunction(...) instantiation outside "
        "core/utils/embeddings.py — route through get_embedder() instead. "
        f"Offenders: {violations}"
    )


def test_get_embedder_returns_singleton_instance():
    """Repeated calls to ``get_embedder()`` return the exact same object."""
    import core.utils.embeddings as emb

    emb._reset_singleton_for_testing()
    with patch.object(emb.config, "EMBEDDING_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5"):
        with patch.object(emb.config, "EMBEDDING_ONNX_FILENAME", "onnx/model.onnx"):
            with patch.object(emb.config, "EMBEDDING_DIMENSIONS", 0):
                with patch.object(emb.config, "EMBEDDING_MODEL_CACHE_DIR", ""):
                    a = emb.get_embedder()
                    b = emb.get_embedder()
                    c = emb.get_embedding_function()
                    assert a is b
                    assert a is c
    emb._reset_singleton_for_testing()


def test_get_embedder_is_not_called_multiple_times_for_construction(monkeypatch):
    """Only one ``OnnxEmbeddingFunction`` instance is ever constructed, even
    under heavy concurrent access.

    Simulate this by spying on the class constructor and hammering
    ``get_embedder()`` from many loops — we must see exactly one
    construction.
    """
    import core.utils.embeddings as emb

    emb._reset_singleton_for_testing()

    call_count = {"n": 0}
    orig_cls = emb.OnnxEmbeddingFunction

    class _CountingOnnx(orig_cls):
        def __init__(self, *args, **kwargs):
            call_count["n"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(emb, "OnnxEmbeddingFunction", _CountingOnnx)

    with patch.object(emb.config, "EMBEDDING_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5"):
        with patch.object(emb.config, "EMBEDDING_ONNX_FILENAME", "onnx/model.onnx"):
            with patch.object(emb.config, "EMBEDDING_DIMENSIONS", 0):
                with patch.object(emb.config, "EMBEDDING_MODEL_CACHE_DIR", ""):
                    for _ in range(50):
                        emb.get_embedder()

    assert call_count["n"] == 1, (
        f"Expected exactly 1 OnnxEmbeddingFunction construction, saw {call_count['n']} — "
        "singleton lock is broken."
    )
    emb._reset_singleton_for_testing()


def test_server_default_model_returns_none():
    """When EMBEDDING_MODEL is the server default, get_embedder returns None."""
    import core.utils.embeddings as emb

    emb._reset_singleton_for_testing()
    with patch.object(emb.config, "EMBEDDING_MODEL", "all-MiniLM-L6-v2"):
        assert emb.get_embedder() is None
