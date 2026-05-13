# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Quenchforge-aware URL routing for the Ollama proxy (v0.93.7).

The ``ollama_proxy`` router exposes the model-listing, model-show, and
model-pull endpoints under ``/ollama/*``.  Until v0.93.7 every endpoint
hard-coded ``OLLAMA_URL`` — so a user with ``INTERNAL_LLM_PROVIDER=
quenchforge`` and Quenchforge bound to a non-default port saw the
Settings → Models page silently hit stock Ollama (or 503 when stock
Ollama wasn't installed).

These tests pin the v0.93.7 behavior:

1. Default — no provider selected — uses ``OLLAMA_URL``.
2. ``INTERNAL_LLM_PROVIDER=quenchforge`` + ``QUENCHFORGE_URL`` set →
   proxy routes to ``QUENCHFORGE_URL``.
3. ``INTERNAL_LLM_PROVIDER=quenchforge`` + ``QUENCHFORGE_URL`` UNSET
   → falls back to ``OLLAMA_URL`` (same-port coincidence remains the
   working default).
4. ``_ollama_enabled()`` returns True for either ``OLLAMA_ENABLED=true``
   OR ``INTERNAL_LLM_PROVIDER=quenchforge`` — so the Models page works
   against a Quenchforge-only install.
"""

from __future__ import annotations

from app.routers.ollama_proxy import _ollama_base_url, _ollama_enabled


def _clear_env(monkeypatch) -> None:
    for var in (
        "OLLAMA_URL",
        "QUENCHFORGE_URL",
        "INTERNAL_LLM_PROVIDER",
        "OLLAMA_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)


def test_base_url_defaults_to_ollama_localhost(monkeypatch):
    _clear_env(monkeypatch)
    assert _ollama_base_url() == "http://localhost:11434"


def test_base_url_uses_ollama_url_when_provider_unset(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_URL", "http://10.0.0.1:11434")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://10.0.0.2:11500")
    # No provider selection → Ollama URL wins regardless of QUENCHFORGE_URL.
    assert _ollama_base_url() == "http://10.0.0.1:11434"


def test_base_url_switches_to_quenchforge_when_provider_selected(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_URL", "http://10.0.0.1:11434")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://10.0.0.2:11500")
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    # Provider=quenchforge AND QUENCHFORGE_URL set → routes to Quenchforge.
    assert _ollama_base_url() == "http://10.0.0.2:11500"


def test_base_url_falls_back_when_quenchforge_url_unset(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_URL", "http://10.0.0.1:11434")
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    # Provider=quenchforge but no QUENCHFORGE_URL → fall back to OLLAMA_URL
    # (preserves the same-port coincidence path that worked pre-v0.93.7).
    assert _ollama_base_url() == "http://10.0.0.1:11434"


def test_base_url_provider_case_insensitive(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("QUENCHFORGE_URL", "http://qf:11500")
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "QuenchForge")
    assert _ollama_base_url() == "http://qf:11500"


def test_base_url_provider_other_values_use_ollama(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_URL", "http://ol:11434")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://qf:11500")
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    # Any provider other than quenchforge → Ollama URL.
    assert _ollama_base_url() == "http://ol:11434"


def test_enabled_returns_true_when_ollama_enabled_set(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    assert _ollama_enabled() is True


def test_enabled_returns_true_when_provider_is_quenchforge(monkeypatch):
    """The Settings → Models page must work against a Quenchforge-only install
    even if OLLAMA_ENABLED was never explicitly flipped on."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    assert _ollama_enabled() is True


def test_enabled_returns_false_when_neither_signal(monkeypatch):
    _clear_env(monkeypatch)
    assert _ollama_enabled() is False


def test_enabled_returns_false_when_provider_is_other(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    assert _ollama_enabled() is False
