# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Feature flags and toggles say what the running code does (audit group F).

- F31: GET /settings reported ``enable_encryption`` from ENABLE_ENCRYPTION, an
  env var nothing else read, so it said "off" while a key was encrypting and
  "on" when nothing was.
- F49: five FEATURE_FLAGS entries had no reader; flipping them disabled nothing.
- F51: FEATURE_TOGGLES is the registry of this module's ENABLE_* toggles, but
  several were missing from it, and set_toggle() did not reach the copies the
  parent-child readers consult.
The real modules run; only the environment and module attributes are set.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
from pathlib import Path

import pytest

import config
import config.features as features_mod
from utils.features import is_toggle_enabled, set_toggle

_REPO = Path(__file__).resolve().parents[3]
_MCP = _REPO / "src" / "mcp"


# ── F31: the encryption state GET /settings reports ──────────────────────────


@pytest.fixture
def fresh_encryptor(monkeypatch: pytest.MonkeyPatch):
    from utils.encryption import reset_encryptor

    monkeypatch.delenv("ENABLE_ENCRYPTION", raising=False)
    reset_encryptor()
    yield monkeypatch
    reset_encryptor()


def _reported_encryption() -> bool:
    from app.routers.settings import get_settings_endpoint

    return asyncio.run(get_settings_endpoint())["enable_encryption"]


def test_settings_report_encryption_on_when_a_key_is_loaded(fresh_encryptor) -> None:
    from cryptography.fernet import Fernet

    from utils.encryption import encrypt_field

    fresh_encryptor.setenv("CERID_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert encrypt_field("secret").startswith("enc:v1:")
    assert _reported_encryption() is True


def test_settings_report_encryption_off_without_a_key(fresh_encryptor) -> None:
    from utils.encryption import encrypt_field

    fresh_encryptor.delenv("CERID_ENCRYPTION_KEY", raising=False)
    fresh_encryptor.setenv("ENABLE_ENCRYPTION", "true")
    assert encrypt_field("secret") == "secret"
    assert _reported_encryption() is False


# ── F49: every feature flag is consulted somewhere ───────────────────────────

# Announced, not built, and a support commitment: none of these has code to gate.
_NO_READER_BY_DESIGN = features_mod.PLANNED_FEATURES | {"priority_support"}
# Flags with no reader that are not yet decided. Shrink-only: a flag that gains
# a reader must leave this set, and nothing may join it.
_NO_READER_OPEN = frozenset({
    "audio_transcription",  # back-compat alias; no call site consults it
    "basic_workflows",
    "calendar_sync",        # back-compat alias; connectors gate on the per-provider flags
    "encryption_at_rest",   # entitlement only; CERID_ENCRYPTION_KEY decides
})


def _reader_sources() -> dict[Path, str]:
    roots = (_MCP, _REPO / "src" / "web" / "src", _REPO / "packages" / "desktop" / "src")
    out: dict[Path, str] = {}
    for root in roots:
        for path in root.rglob("*"):
            parts = set(path.parts)
            if path.suffix not in {".py", ".ts", ".tsx", ".json"}:
                continue
            if parts & {"tests", "__tests__", "node_modules", "dist"}:
                continue
            if path == _MCP / "config" / "features.py":
                continue
            out[path] = path.read_text(errors="ignore")
    return out


def _flags_without_a_reader() -> set[str]:
    sources = _reader_sources()
    missing = set()
    for flag in features_mod.FEATURE_FLAGS:
        quoted = re.compile(rf"[\"']{re.escape(flag)}[\"']")
        if not any(quoted.search(text) for text in sources.values()):
            missing.add(flag)
    return missing


def test_every_feature_flag_has_a_reader() -> None:
    unexplained = _flags_without_a_reader() - _NO_READER_BY_DESIGN - _NO_READER_OPEN
    assert not unexplained, (
        f"FEATURE_FLAGS entries nothing consults — flipping them disables nothing: {sorted(unexplained)}"
    )


def test_the_open_list_only_shrinks() -> None:
    assert _NO_READER_OPEN <= _flags_without_a_reader(), (
        "a flag in _NO_READER_OPEN now has a reader; remove it from the list"
    )


def test_the_tier_matrix_shows_ungated_desktop_features_without_a_gate() -> None:
    matrix = (_REPO / "docs" / "TIER_MATRIX.md").read_text()
    rows = [line for line in matrix.splitlines() if line.startswith("| ")]
    gates = {m.group(1) for line in rows if (m := re.search(r"\| `([a-z_]+)` \|$", line))}
    assert gates <= set(features_mod.FEATURE_FLAGS), (
        f"the tier matrix names gates that are not flags: {sorted(gates - set(features_mod.FEATURE_FLAGS))}"
    )
    assert any(line.startswith("| Menu-bar mode |") and "none (always on)" in line for line in rows)


# ── F51: the toggle registry covers this module's toggles ────────────────────


def _module_enable_toggles() -> set[str]:
    tree = ast.parse((_MCP / "config" / "features.py").read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("ENABLE_"):
                    names.add(target.id)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname for a in node.names if a.asname and a.asname.startswith("ENABLE_"))
    return names


def test_every_enable_toggle_in_the_module_is_registered() -> None:
    registered = {name.upper() for name in features_mod.FEATURE_TOGGLES}
    assert _module_enable_toggles() == registered


@pytest.mark.parametrize("name", sorted(features_mod.FEATURE_TOGGLES))
def test_a_registered_toggle_reaches_the_module_and_the_package(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config.settings as settings_mod
    import utils.chunker as chunker_mod

    # set_toggle also writes these for two toggles; register them for restore.
    monkeypatch.setenv("ENABLE_PARENT_CHILD_RETRIEVAL", os.getenv("ENABLE_PARENT_CHILD_RETRIEVAL", "false"))
    monkeypatch.setattr(chunker_mod, "PARENT_CHILD_ENABLED", chunker_mod.PARENT_CHILD_ENABLED)
    monkeypatch.setattr(settings_mod, "SMART_ROUTING_ENABLED", settings_mod.SMART_ROUTING_ENABLED)
    monkeypatch.setattr(config, "SMART_ROUTING_ENABLED", config.SMART_ROUTING_ENABLED)
    attr = name.upper()
    before = features_mod.FEATURE_TOGGLES[name]
    try:
        set_toggle(name, not before)
        assert is_toggle_enabled(name) is (not before)
        assert getattr(features_mod, attr) is (not before)
        assert getattr(config, attr) is (not before)
    finally:
        set_toggle(name, before)


def test_the_startup_log_names_the_dark_paths(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="ai-companion.config"):
        features_mod.log_feature_toggles()
    logged = caplog.text
    for name in ("enable_fact_writes", "enable_session_summarization", "enable_surface_biased_retrieval"):
        assert name in logged


def _top_level_toggle_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    found = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {"config", "config.features"}:
            found += [a.name for a in node.names if a.name.startswith("ENABLE_")]
    return found


def test_no_module_copies_a_toggle_at_import_time() -> None:
    """A module-level ``from config import ENABLE_X`` freezes the value set_toggle changes."""
    offenders = {
        str(path.relative_to(_MCP)): names
        for path in _MCP.rglob("*.py")
        if "tests" not in path.parts and path.parent != _MCP / "config"
        and (names := [n for n in _top_level_toggle_imports(path) if n.lower() in features_mod.FEATURE_TOGGLES])
    }
    assert not offenders


@pytest.fixture
def parent_child_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import utils.chunker as chunker_mod

    monkeypatch.setenv("ENABLE_PARENT_CHILD_RETRIEVAL", "false")
    monkeypatch.setattr(chunker_mod, "PARENT_CHILD_ENABLED", False)
    monkeypatch.setattr(features_mod, "ENABLE_PARENT_CHILD_RETRIEVAL", False)
    monkeypatch.setattr(config, "ENABLE_PARENT_CHILD_RETRIEVAL", False, raising=False)
    monkeypatch.setitem(features_mod.FEATURE_TOGGLES, "enable_parent_child_retrieval", False)
    monkeypatch.setitem(features_mod.FEATURE_FLAGS, "parent_child_retrieval", True)


def test_set_toggle_turns_parent_child_on_for_query_ingest_and_recommender(parent_child_off) -> None:
    import utils.chunker as chunker_mod
    from app.processor.jobs.config_recommender import _read_flag_state
    from core.agents.query_agent import _parent_child_enabled

    set_toggle("enable_parent_child_retrieval", True)

    assert _parent_child_enabled() is True
    assert chunker_mod.parent_child_enabled() is True
    assert "ENABLE_PARENT_CHILD_RETRIEVAL" in _read_flag_state()

    set_toggle("enable_parent_child_retrieval", False)

    assert _parent_child_enabled() is False
    assert chunker_mod.parent_child_enabled() is False
    assert "ENABLE_PARENT_CHILD_RETRIEVAL" not in _read_flag_state()
