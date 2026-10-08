# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Adding content again works whether or not it was forgotten before.

Explicit ingest (upload, /ingest, SDK, MCP, agents) re-adds a forgotten artifact
and records ``readded`` against the forget it cancels, so the content imports on
every machine. Automatic paths (folder rescans, scheduled mail polling, source
connectors) skip it instead, or forgetting a file would undo itself at
the next poll.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from core.forget.registry import Entry, Registry, Subject

_MCP = Path(__file__).resolve().parents[1]


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    registry = Registry(tmp_path / "forget", "m1")
    monkeypatch.setattr("core.forget.registry.get_registry", lambda: registry)
    return registry


def _forget(reg: Registry, artifact_id: str) -> None:
    reg.append([Entry("fg_1", Subject("artifact", artifact_id), "purged", "2026-10-07T10:00:00Z", "m1", "ui")])


def test_explicit_ingest_readds_a_forgotten_artifact(reg):
    from app.services.ingestion import _forget_gate
    _forget(reg, "a1")
    assert _forget_gate("a1", "readd") is None
    assert reg.state_of("artifact", "a1") == "readded"
    assert not reg.is_forgotten("artifact", "a1")


def test_automatic_ingest_skips_a_forgotten_artifact(reg):
    from app.services.ingestion import _forget_gate
    _forget(reg, "a1")
    result = _forget_gate("a1", "skip")
    assert result is not None and result["status"] == "skipped" and result["reason"] == "forgotten"
    assert result["artifact_id"] == "a1"
    assert reg.is_forgotten("artifact", "a1")


def test_content_never_forgotten_passes_without_touching_the_registry(reg):
    from app.services.ingestion import _forget_gate
    assert _forget_gate("fresh", "readd") is None
    assert _forget_gate("fresh", "skip") is None
    assert reg.latest() == []


def test_both_entry_points_default_to_readd():
    from app.services.ingestion import ingest_content, ingest_file
    for fn in (ingest_content, ingest_file):
        assert inspect.signature(fn).parameters["on_forgotten"].default == "readd"


# The automatic ingest paths, by enclosing function: the folder rescan, the
# scheduled IMAP poll and the source-connector sink. User-run imports
# (bookmarks, .emlx files, uploads) are explicit and re-add.
_AUTOMATIC = {
    "app/services/folder_scanner.py": ("scan_folder", 2),
    "app/data_sources/email_imap.py": ("poll_email", 1),
    "app/main.py": ("_source_ingest_fn", 1),
}
_NAMES = {"ingest_content", "ingest_file", "_ingest_content"}


def _ingest_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in _NAMES:
            calls.append(node)
        elif name == "to_thread" and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in _NAMES:
            calls.append(node)
    return calls


def _function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


@pytest.mark.parametrize("rel, spec", sorted(_AUTOMATIC.items()))
def test_every_automatic_ingest_call_skips_forgotten_content(rel, spec):
    func_name, expected = spec
    calls = _ingest_calls(_function(ast.parse((_MCP / rel).read_text(encoding="utf-8")), func_name))
    assert len(calls) >= expected, f"{rel}::{func_name}: expected {expected} ingest call(s), found {len(calls)}"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        value = kw.get("on_forgotten")
        assert isinstance(value, ast.Constant) and value.value == "skip", (
            f"{rel}:{call.lineno} ingests without on_forgotten='skip'"
        )
