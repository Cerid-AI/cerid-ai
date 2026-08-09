# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for PR 2 — provider routing: quenchforge as a first-class local backend.

Covers the four user-visible surfaces:

1. ``PROVIDER_CONFIGS`` exposes a ``quenchforge`` entry with the expected shape
   so the Settings → Providers page can render it.
2. ``load_config()`` enables the ``quenchforge`` provider when
   ``INTERNAL_LLM_PROVIDER=quenchforge`` (and does NOT enable it otherwise).
3. ``get_degraded_status()`` treats an enabled quenchforge as a configured LLM
   so the system isn't reported as degraded when only quenchforge is selected.
4. ``OllamaLLMClient`` accepts both ``ollama`` and ``quenchforge`` and rejects
   anything else, then routes through ``_call_ollama`` with the correct URL.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.stores.llm_clients import OllamaLLMClient
from core.routing.model_providers import (
    PROVIDER_CONFIGS,
    ModelProviderConfig,
    ProviderState,
    get_degraded_status,
    load_config,
)

# ---------------------------------------------------------------------------
# 1. PROVIDER_CONFIGS shape
# ---------------------------------------------------------------------------


def test_quenchforge_provider_registered() -> None:
    """The Settings UI relies on PROVIDER_CONFIGS for the provider list."""
    assert "quenchforge" in PROVIDER_CONFIGS
    entry = PROVIDER_CONFIGS["quenchforge"]
    assert entry["env_var"] == "QUENCHFORGE_URL"
    assert entry.get("is_local") is True
    assert "github.com/cerid-ai/quenchforge" in entry["signup_url"]


def test_quenchforge_does_not_displace_ollama() -> None:
    """Both local backends coexist — the user picks via INTERNAL_LLM_PROVIDER."""
    assert "ollama" in PROVIDER_CONFIGS
    assert "quenchforge" in PROVIDER_CONFIGS
    assert PROVIDER_CONFIGS["ollama"]["env_var"] == "OLLAMA_URL"
    assert PROVIDER_CONFIGS["quenchforge"]["env_var"] == "QUENCHFORGE_URL"


# ---------------------------------------------------------------------------
# 2. load_config() activation rules
# ---------------------------------------------------------------------------


def test_load_config_enables_quenchforge_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    cfg = load_config(redis_client=None)
    assert cfg.providers["quenchforge"].enabled is True
    # Ollama remains gated on its own flag, untouched.
    monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
    cfg = load_config(redis_client=None)
    assert cfg.providers["ollama"].enabled is False


def test_load_config_quenchforge_disabled_when_not_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    cfg = load_config(redis_client=None)
    assert cfg.providers["quenchforge"].enabled is False


def test_load_config_quenchforge_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "Quenchforge")
    cfg = load_config(redis_client=None)
    assert cfg.providers["quenchforge"].enabled is True


def test_load_config_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://192.168.1.42:11434")
    cfg = load_config(redis_client=None)
    assert cfg.providers["quenchforge"].url == "http://192.168.1.42:11434"


# ---------------------------------------------------------------------------
# 3. Degraded-mode detection
# ---------------------------------------------------------------------------


def test_quenchforge_alone_is_not_degraded() -> None:
    """A user on quenchforge with no API keys is fully operational."""
    cfg = ModelProviderConfig(
        providers={
            "openrouter": ProviderState(enabled=False, api_key=""),
            "ollama": ProviderState(enabled=False),
            "quenchforge": ProviderState(enabled=True, url="http://host.docker.internal:11434"),
        }
    )
    status = get_degraded_status(cfg)
    assert status["degraded"] is False


def test_no_providers_enabled_is_degraded() -> None:
    cfg = ModelProviderConfig(
        providers={
            "openrouter": ProviderState(enabled=False, api_key=""),
            "ollama": ProviderState(enabled=False),
            "quenchforge": ProviderState(enabled=False),
        }
    )
    status = get_degraded_status(cfg)
    assert status["degraded"] is True


# ---------------------------------------------------------------------------
# 4. OllamaLLMClient — accepts both backends, rejects others
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["ollama", "quenchforge"])
def test_ollama_llm_client_accepts_valid_provider(provider: str) -> None:
    client = OllamaLLMClient(provider=provider)
    assert client._provider == provider  # noqa: SLF001 — verifying constructor wired the field


def test_ollama_llm_client_default_is_ollama() -> None:
    assert OllamaLLMClient()._provider == "ollama"  # noqa: SLF001


@pytest.mark.parametrize("bad", ["openrouter", "anthropic", "openai", "OLLAMA", "", "quench"])
def test_ollama_llm_client_rejects_invalid_provider(bad: str) -> None:
    with pytest.raises(ValueError, match="OllamaLLMClient supports"):
        OllamaLLMClient(provider=bad)


# ---------------------------------------------------------------------------
# URL-swap behavior: _call_ollama hits the right base URL per provider
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.mark.parametrize(
    "provider, ollama_url, quenchforge_url, expected_url",
    [
        ("ollama", "http://ollama-host:11434", "http://quench-host:11434", "http://ollama-host:11434"),
        ("quenchforge", "http://ollama-host:11434", "http://quench-host:11434", "http://quench-host:11434"),
        # Quenchforge falls back to OLLAMA_URL when QUENCHFORGE_URL is unset.
        ("quenchforge", "http://ollama-host:11434", "", "http://ollama-host:11434"),
    ],
)
@pytest.mark.asyncio
async def test_call_ollama_url_selection(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    ollama_url: str,
    quenchforge_url: str,
    expected_url: str,
) -> None:
    """The wire format is identical; only the URL changes per provider."""
    from core.utils import internal_llm as mod

    monkeypatch.setenv("OLLAMA_URL", ollama_url)
    monkeypatch.setattr(mod.config, "QUENCHFORGE_URL", quenchforge_url, raising=False)
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "test-model:latest", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)

    fake_client = MagicMock()
    captured: dict[str, str] = {}

    async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001 — payload not under test
        captured["url"] = url
        return _FakeResponse({"message": {"content": "ok"}})

    fake_client.post = _post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))

    # Bypass the circuit breaker so the call path runs directly.
    class _PassThroughBreaker:
        async def call(self, fn):  # type: ignore[no-untyped-def]
            return await fn()

    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())

    out = await mod._call_ollama(  # noqa: SLF001 — internal helper under test
        [{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=10,
        provider=provider,
    )
    assert out == "ok"
    assert captured["url"] == f"{expected_url}/api/chat"
