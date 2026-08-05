# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-3e-2a verifiability harness — BYOK DIRECT-DISPATCH probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-008).

CR-008 (CONFIRMED): PUT /providers/config collects, validates, stores, and
env-exports direct-provider keys (openai/anthropic/xai/google), but the only
consumer chain (``route_with_failover`` → ``resolve_provider_for_model``) has
ZERO production callers and is prefix-broken, so every completion authenticates
with ``OPENROUTER_API_KEY`` only. A user with a valid direct key and no OpenRouter
credit sees BYOK validate green, then every chat 401s/402s against OpenRouter.

3e-2a wires BYOK for the OpenAI-compatible providers (openai + xai) across BOTH
production transports — ``core.utils.llm_client.call_llm`` (pipeline/content) and
``app.routers.chat`` (user-facing streaming) — via a single env authority
(``core.routing.provider_state``). Anthropic/Google adapters land in 3e-2b/2c.

**Seam = env authority (operator decision 2026-07-20):** os.environ is the single
canonical runtime plane (Phase 3a/3b). The enable path + boot projection write a
coherent ``BYOK_DIRECT_PROVIDERS`` marker; both transports resolve through
``provider_state.byok_target``. The gate is enable-intent (the marker) AND key
presence — NOT bare key presence, so a stray ``OPENAI_API_KEY`` set by the setup
wizard for another purpose does NOT silently reroute traffic off OpenRouter.

RED-then-GREEN; synthetic (no live stack) so it runs in ci-local.
"""
from __future__ import annotations

from typing import Any

import pytest

# NOTE: only the two call_llm dispatch tests are async — they carry an explicit
# @pytest.mark.asyncio. A module-level asyncio mark would mis-tag the sync tests.

# Fake keys hoisted to constants so the secret-scanner sees each literal once (a
# repeated literal in a keyword context trips detect-secrets per occurrence).
_K_OPENAI = "sk-openai-live"  # pragma: allowlist secret
_K_XAI = "xai-live"  # pragma: allowlist secret
_K_ANTHROPIC = "sk-ant-live"  # pragma: allowlist secret
_K_OPENROUTER = "sk-or-live"  # pragma: allowlist secret
_K_STRAY = "sk-stray-not-for-chat"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# provider_state.byok_target — the gated env authority
# ---------------------------------------------------------------------------


def _clear_byok_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "BYOK_DIRECT_PROVIDERS",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_byok_target_resolves_openai_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """openai in the enable marker + a key present → direct OpenAI target with the
    native model id (openrouter/ and vendor prefix stripped)."""
    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", _K_OPENAI)

    target = provider_state.byok_target("openrouter/openai/gpt-4o-mini")

    assert target is not None, "openai enabled with a key must resolve a direct target (CR-008)"
    assert target.provider == "openai"
    assert target.base_url == "https://api.openai.com/v1"
    assert target.api_key == _K_OPENAI  # pragma: allowlist secret
    assert target.model == "gpt-4o-mini", "direct API wants the native id, not the vendor-prefixed one"


def test_byok_target_resolves_xai_openai_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    """xAI speaks the OpenAI wire → dispatchable in 3e-2a."""
    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "xai")
    monkeypatch.setenv("XAI_API_KEY", _K_XAI)

    target = provider_state.byok_target("openrouter/x-ai/grok-4.20")

    assert target is not None
    assert target.provider == "xai"
    assert target.base_url == "https://api.x.ai/v1"
    assert target.api_key == _K_XAI  # pragma: allowlist secret
    assert target.model == "grok-4.20"


def test_byok_target_none_for_stray_key_without_enable_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stray OPENAI_API_KEY (e.g. set by the setup wizard for another purpose)
    must NOT reroute openai/* off OpenRouter — enable-intent gates dispatch, not
    bare key presence (CR-008 correctness note)."""
    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", _K_STRAY)
    # BYOK_DIRECT_PROVIDERS intentionally unset.

    assert provider_state.byok_target("openrouter/openai/gpt-4o-mini") is None


def test_byok_target_none_for_openrouter_native_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """meta-llama/* is served only via OpenRouter (aggregator) — never a direct target."""
    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", _K_OPENAI)

    assert provider_state.byok_target("openrouter/meta-llama/llama-3.3-70b-instruct") is None


def test_project_byok_env_writes_coherent_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The coherent writer projects {provider: key} into the canonical env plane:
    the marker lists the enabled providers and each key is present for byok_target."""
    import os

    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    provider_state.project_byok_env({"openai": _K_OPENAI, "xai": _K_XAI})

    assert set(os.environ["BYOK_DIRECT_PROVIDERS"].split(",")) == {"openai", "xai"}
    assert os.environ["OPENAI_API_KEY"] == _K_OPENAI
    # Round-trips through the reader.
    assert provider_state.byok_target("openrouter/openai/gpt-4o-mini") is not None

    # Empty projection clears the marker (a disable must not leave a stale route).
    provider_state.project_byok_env({})
    assert os.environ.get("BYOK_DIRECT_PROVIDERS", "") == ""


# ---------------------------------------------------------------------------
# resolve_provider_for_model — the CR-008 prefix defect
# ---------------------------------------------------------------------------


def test_resolve_provider_for_model_strips_openrouter_prefix() -> None:
    """Tier ids carry the ``openrouter/`` prefix; MODEL_TO_PROVIDER keys are bare.
    Pre-fix the direct-key branch could never fire (CR-008). After the strip, a
    configured direct key resolves for the native provider."""
    from core.routing.model_providers import (
        ModelProviderConfig,
        ProviderState,
        resolve_provider_for_model,
    )

    cfg = ModelProviderConfig(
        providers={"anthropic": ProviderState(enabled=True, api_key=_K_ANTHROPIC)}
    )
    provider, key = resolve_provider_for_model("openrouter/anthropic/claude-sonnet-4.6", cfg)

    assert provider == "anthropic", "openrouter/ prefix must be stripped before the prefix match (CR-008)"
    assert key == _K_ANTHROPIC


def test_enabled_direct_providers_excludes_aggregator_and_local() -> None:
    """The boot/PUT projector source: direct = not aggregator (openrouter) and not
    local (ollama/quenchforge), enabled, with a key."""
    from core.routing.model_providers import (
        ModelProviderConfig,
        ProviderState,
        enabled_direct_providers,
    )

    cfg = ModelProviderConfig(
        providers={
            "openrouter": ProviderState(enabled=True, api_key="or-key"),
            "openai": ProviderState(enabled=True, api_key="sk-openai"),
            "xai": ProviderState(enabled=False, api_key="xai-key"),  # disabled → excluded
            "anthropic": ProviderState(enabled=True, api_key=""),  # keyless → excluded
            "ollama": ProviderState(enabled=True, url="http://localhost:11434"),
        }
    )
    result = enabled_direct_providers(cfg)

    assert result == {"openai": "sk-openai"}


# ---------------------------------------------------------------------------
# call_llm — direct dispatch vs byte-identical OpenRouter fallback
# ---------------------------------------------------------------------------


class _RecordingResponse:
    status_code = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


class _RecordingClient:
    def __init__(self, body: dict[str, Any]) -> None:
        self.posts: list[dict[str, Any]] = []
        self._body = body

    async def post(self, url: str, **kwargs: Any) -> _RecordingResponse:
        self.posts.append({"url": url, **kwargs})
        return _RecordingResponse(self._body)

    @property
    def is_closed(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class _RecordingCtx:
    def __init__(self, client: _RecordingClient, base_url: str = "") -> None:
        self._client = client
        self.base_url = base_url

    async def __aenter__(self) -> _RecordingClient:
        return self._client

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_call_llm_dispatches_direct_when_byok_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With openai BYOK enabled, call_llm authenticates against the OpenAI base_url
    with the direct key — NOT OpenRouter — and sends the native model id."""
    from core.routing import provider_state  # noqa: F401 — ensures module importable
    from core.utils import llm_client

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", _K_OPENAI)
    # Deliberately NO OpenRouter key — a BYOK-only user must still succeed.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    rec = _RecordingClient({"choices": [{"message": {"content": "hi from openai"}}]})
    captured: dict[str, str] = {}

    def _fake_direct(base_url: str) -> _RecordingCtx:
        captured["base_url"] = base_url
        return _RecordingCtx(rec, base_url)

    monkeypatch.setattr(llm_client, "_acquire_direct_client", _fake_direct, raising=True)

    out = await llm_client.call_llm(
        [{"role": "user", "content": "hello"}],
        model="openrouter/openai/gpt-4o-mini",
    )

    assert out == "hi from openai"
    assert captured["base_url"] == "https://api.openai.com/v1"
    assert len(rec.posts) == 1
    post = rec.posts[0]
    assert post["headers"]["Authorization"] == "Bearer sk-openai-live"
    assert post["json"]["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_call_llm_unchanged_when_no_byok(monkeypatch: pytest.MonkeyPatch) -> None:
    """No BYOK enabled → the OpenRouter transport is used byte-identically
    (OpenRouter key, OpenRouter breaker path)."""
    from core.utils import llm_client

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", _K_OPENROUTER)

    rec = _RecordingClient({"choices": [{"message": {"content": "hi from openrouter"}}]})
    monkeypatch.setattr(llm_client, "_acquire_client", lambda: _RecordingCtx(rec), raising=True)
    # If the direct seam is invoked at all, fail loudly.
    monkeypatch.setattr(
        llm_client,
        "_acquire_direct_client",
        lambda base_url: (_ for _ in ()).throw(AssertionError("direct seam used without BYOK")),
        raising=False,
    )

    out = await llm_client.call_llm(
        [{"role": "user", "content": "hello"}],
        model="openrouter/openai/gpt-4o-mini",
    )

    assert out == "hi from openrouter"
    assert rec.posts[0]["headers"]["Authorization"] == "Bearer sk-or-live"


# ---------------------------------------------------------------------------
# chat.py — the user-facing streaming transport (CR-008's failure scenario)
# ---------------------------------------------------------------------------


def test_chat_dispatch_targets_direct_provider_when_byok(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat transport resolves the same env authority: a BYOK openai model
    dispatches to the OpenAI base_url + direct key + native model, so a BYOK-only
    user's chat no longer 401s against OpenRouter (CR-008)."""
    from app.routers import chat

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", _K_OPENAI)

    base_url, key, model, wire = chat._resolve_chat_dispatch("openai/gpt-4o-mini", "or-user-key")

    assert base_url == "https://api.openai.com/v1"
    assert key == _K_OPENAI, "direct key takes precedence over the per-user OpenRouter key"
    assert model == "gpt-4o-mini"
    assert wire == "openai"


def test_chat_dispatch_uses_openrouter_without_byok(monkeypatch: pytest.MonkeyPatch) -> None:
    """No BYOK → OpenRouter base_url + the resolved OpenRouter key + the vendor-
    prefixed model id (OpenRouter's naming)."""
    from app.routers import chat

    _clear_byok_env(monkeypatch)

    base_url, key, model, wire = chat._resolve_chat_dispatch("openai/gpt-4o-mini", "or-user-key")

    assert base_url == chat.OPENROUTER_BASE
    assert key == "or-user-key"
    assert model == "openai/gpt-4o-mini"
    assert wire == "openai"
