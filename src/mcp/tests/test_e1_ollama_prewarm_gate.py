# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-5 — the startup Ollama pre-warm gate must read the ENV plane.

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-109). The lifespan gated the Ollama client pre-warm on
``getattr(config, "OLLAMA_ENABLED", False)``, but ``OLLAMA_ENABLED`` is an env
var — the ``config`` package defines no such attribute — so the gate was always
False and the pre-warm never ran, even with ``OLLAMA_ENABLED=true`` set. The fix
reads the env plane via ``core.routing.provider_state.ollama_enabled``.

These probes guard the root cause (a config-attribute gate is dead) and the
env-plane function the fix now uses. No live stack.
"""
from __future__ import annotations

import pytest


def test_config_exposes_no_ollama_enabled_attribute():
    """The dead-gate root cause: OLLAMA_ENABLED lives only on the env plane, so
    any getattr(config, "OLLAMA_ENABLED", ...) gate is dead. If this ever starts
    failing, the plane confusion has been (accidentally) papered over rather than
    fixed at the call site."""
    import config

    sentinel = object()
    assert getattr(config, "OLLAMA_ENABLED", sentinel) is sentinel, (
        "config now exposes an OLLAMA_ENABLED attribute — main.py must still gate "
        "the pre-warm on the env plane (ollama_enabled()), not this attribute"
    )


@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("1", True), ("yes", True), ("on", True),
     ("false", False), ("", False), ("0", False)],
)
def test_ollama_enabled_reads_env_plane(monkeypatch, value, expected):
    """ollama_enabled() — the gate main.py now uses — reflects the env var."""
    import core.routing.provider_state as ps

    monkeypatch.setenv("OLLAMA_ENABLED", value)
    assert ps.ollama_enabled() is expected


def test_ollama_enabled_false_when_unset(monkeypatch):
    monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
    import core.routing.provider_state as ps

    assert ps.ollama_enabled() is False


def test_main_prewarm_block_gates_on_ollama_enabled():
    """R14: the lifespan pre-warm must call ollama_enabled() and _get_ollama_client.

    Static contract so a revert of the main.py pre-warm body fails without a
    live stack (the prior test only guarded the dead getattr root cause).
    """
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8",
    )
    assert "ollama_enabled()" in main_src
    assert "_get_ollama_client" in main_src
    assert "Ollama HTTP client pool pre-warmed" in main_src
