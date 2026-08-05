# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Single runtime authority for which provider serves inference (E1 Phase 3a).

Runtime provider configuration previously lived in four disjoint planes
(``os.environ``, ``config`` module attrs, Redis, ``.env``) with no authority, so
a runtime switch reached some readers and not others (split-brain: CR-007/100),
telemetry fabricated a per-stage table (CR-024), and the local dispatch URL was
read from the wrong knob (CR-098).

**Canonical plane = ``os.environ``.** It is what the dispatch-side truth surfaces
(``inference_routing.get_routing_snapshot``, ``smart_router._check_ollama``)
already read, and it is re-read at call time so a runtime write is reflected
immediately. Every reader resolves provider identity through THIS module instead
of touching ``os.environ`` / ``config`` attrs / Redis directly, collapsing the
planes to one resolution order; writers call :func:`set_active_provider` so a
switch lands on the canonical plane.

Phase 3b routes all five write endpoints through :func:`set_active_provider`,
extends the ``module-getenv`` lint gate to make this module the *only* reader of
the provider-state keys, and drops the transitional ``config``-attr mirror.
"""
from __future__ import annotations

import os
from typing import NamedTuple

# Both dispatch to a local daemon on this host. quenchforge is an Ollama-API-
# compatible local server (:11434); treating only "ollama" as local is exactly
# the CR-024 telemetry bug.
_LOCAL_PROVIDERS = ("ollama", "quenchforge")
_DEFAULT_LOCAL_URL = "http://localhost:11434"
_TRUTHY = {"1", "true", "yes", "on"}

# E1 CR-008: direct BYOK providers mapped to the completion WIRE their adapter
# speaks. ``openai``/``xai`` share the OpenAI /chat/completions shape (3e-2a);
# ``anthropic`` uses the Messages API (3e-2b); ``google`` uses Gemini's
# generateContent API (3e-2c). A direct provider absent from this map resolves to
# ``None`` in ``byok_target`` (the call falls through to OpenRouter rather than
# POSTing an incompatible body). The wire tag lets each transport pick the right
# adapter.
_DIRECT_WIRE = {
    "openai": "openai",
    "xai": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
}

# Anthropic Messages-API version pin (sent as the ``anthropic-version`` header).
# Shared by every BYOK Anthropic transport (llm_client + chat) so the pin can't
# drift between them.
ANTHROPIC_VERSION = "2023-06-01"

# The canonical env marker listing the direct providers the operator has
# EXPLICITLY enabled (with a key) — written coherently by :func:`project_byok_env`
# from the persisted config at boot and on every PUT /providers/config. Enable
# intent lives HERE, never in bare key presence: an ``OPENAI_API_KEY`` set by the
# setup wizard for another purpose must not silently reroute traffic (CR-008).
_BYOK_MARKER = "BYOK_DIRECT_PROVIDERS"


def active_provider() -> str:
    """The provider serving the global internal-LLM default, from the canonical
    (env) plane. One of ``"openrouter"`` / ``"ollama"`` / ``"quenchforge"``."""
    return os.getenv("INTERNAL_LLM_PROVIDER", "openrouter").strip().lower()


def is_local_provider(provider: str | None = None) -> bool:
    """True when the provider dispatches to a local daemon on this host — BOTH
    ``ollama`` and ``quenchforge``. Pass ``provider`` to test a specific value;
    omit to test the active one."""
    p = (provider if provider is not None else active_provider()).strip().lower()
    return p in _LOCAL_PROVIDERS


def local_backend_url(provider: str | None = None) -> str:
    """Base URL for the local daemon. ``quenchforge`` → ``QUENCHFORGE_URL``
    (falling back to ``OLLAMA_URL``); anything else → ``OLLAMA_URL``. Pre-fix,
    ``_call_ollama_direct`` read ``OLLAMA_URL`` even for quenchforge, so the probe
    validated one daemon and the call hit another (CR-098)."""
    p = (provider if provider is not None else active_provider()).strip().lower()
    if p == "quenchforge":
        return os.getenv("QUENCHFORGE_URL") or os.getenv("OLLAMA_URL") or _DEFAULT_LOCAL_URL
    return os.getenv("OLLAMA_URL", _DEFAULT_LOCAL_URL)


def ollama_enabled() -> bool:
    """Whether the ``OLLAMA_ENABLED`` gate is set (used by the /health ollama probe)."""
    return os.getenv("OLLAMA_ENABLED", "").strip().lower() in _TRUTHY


# Stage → PROVIDER_* env pin. Must match config.settings.PIPELINE_PROVIDERS keys.
_PIPELINE_STAGE_ENV: dict[str, str] = {
    "claim_extraction": "PROVIDER_CLAIM_EXTRACTION",
    "query_decompose": "PROVIDER_QUERY_DECOMPOSE",
    "topic_extraction": "PROVIDER_TOPIC_EXTRACTION",
    "memory_conflict_resolve": "PROVIDER_MEMORY_CONFLICT_RESOLVE",
    "rerank_llm": "PROVIDER_RERANK_LLM",
}


def rebuild_pipeline_providers(provider: str | None = None) -> None:
    """Rebind ``config.PIPELINE_PROVIDERS`` to the current active provider (E1 CR-007).

    At import time each stage is frozen to the boot ``INTERNAL_LLM_PROVIDER``.
    A runtime switch must rebuild the map so stages without an explicit
    ``PROVIDER_*`` env pin follow the new default; otherwise the 5 live pipeline
    stages keep dispatching to the boot-time provider until restart.
    """
    import config

    active = (
        provider
        if provider is not None
        else (os.getenv("INTERNAL_LLM_PROVIDER") or "openrouter")
    ).strip().lower()
    config.PIPELINE_PROVIDERS = {
        stage: os.getenv(env_key, active)
        for stage, env_key in _PIPELINE_STAGE_ENV.items()
    }


def set_active_provider(provider: str, model: str | None = None) -> None:
    """Write a runtime provider switch to the canonical (env) plane so every
    reader — ``get_routing_snapshot``, ``smart_router``, ``/health``, internal
    dispatch — sees it immediately (E1 CR-007/100). Does NOT persist to ``.env``
    (that is the setup wizard's job).

    A ``config``-attr mirror is written transitionally for the legacy readers not
    yet migrated to this authority (removed in Phase 3b once every reader resolves
    here).
    """
    provider = provider.strip().lower()
    os.environ["INTERNAL_LLM_PROVIDER"] = provider
    if model is not None:
        os.environ["INTERNAL_LLM_MODEL"] = model

    # E1 CR-088/089: the ollama proxy gate and smart-router local gate require
    # OLLAMA_ENABLED for the 'ollama' provider. Set true when switching TO ollama.
    # E1 R6: do NOT force-write false on non-ollama switches — that clobbered the
    # legitimate hybrid (cloud global + local classification/internal stages)
    # whenever settings saved a non-ollama primary. Explicit local disable is the
    # ollama_enabled settings/setup plane, not a side effect of provider switch.
    # quenchforge bypasses the ollama gate by provider name.
    if provider == "ollama":
        os.environ["OLLAMA_ENABLED"] = "true"

    import config  # top-level settings package; always importable in-process
    config.INTERNAL_LLM_PROVIDER = provider
    if model is not None:
        config.INTERNAL_LLM_MODEL = model

    # E1 CR-007: unfreeze per-stage pipeline defaults onto the new provider.
    rebuild_pipeline_providers(provider)


# ---------------------------------------------------------------------------
# BYOK direct-provider dispatch authority (E1 CR-008, Phase 3e-2a)
# ---------------------------------------------------------------------------


class BYOKTarget(NamedTuple):
    """A resolved direct-provider dispatch target for a model.

    ``model`` is the provider-native id (``openrouter/`` and the vendor prefix
    stripped) that the direct API expects — e.g. ``gpt-4o-mini`` for OpenAI, not
    OpenRouter's ``openai/gpt-4o-mini``. ``wire`` names the completion adapter the
    transport must use (``"openai"`` | ``"anthropic"``).
    """

    provider: str
    base_url: str
    api_key: str
    model: str
    wire: str


def byok_enabled_providers() -> frozenset[str]:
    """The direct providers the operator has explicitly enabled, from the canonical
    (env) marker. Empty when no BYOK is configured."""
    raw = os.getenv(_BYOK_MARKER, "")
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def _resolve_model_prefix(model: str) -> tuple[str | None, str]:
    """Map a (possibly ``openrouter/``-prefixed) model id to ``(provider, native)``.

    Strips the ``openrouter/`` tier prefix, then matches the vendor prefix
    (``openai/``, ``x-ai/``, …) from :data:`MODEL_TO_PROVIDER`. Returns
    ``(provider, native_model)`` on a match, else ``(None, stripped_model)``.
    """
    from core.routing.model_providers import MODEL_TO_PROVIDER

    native = model.removeprefix("openrouter/")
    for prefix, provider in MODEL_TO_PROVIDER.items():
        if native.startswith(prefix):
            return provider, native[len(prefix):]
    return None, native


def byok_target(model: str) -> BYOKTarget | None:
    """Resolve a direct-provider dispatch target for ``model``, or ``None`` to use
    OpenRouter.

    Returns a target only when ALL hold (CR-008 gate):
    1. the model is not an OpenRouter-only ``:online`` web-search id (E1 R2 —
       RESEARCH/EXPERT tiers use ``…:online``; direct xAI rejects that suffix;
       mirrors the ``call_llm_raw`` OpenRouter-only decision);
    2. the model's native provider is a direct provider (openai/xai/…);
    3. that provider is in the canonical enable marker (explicit operator intent,
       NOT bare key presence — a stray setup-wizard key must not reroute traffic);
    4. a key is present for it;
    5. an adapter exists for the provider's completion wire (:data:`_DIRECT_WIRE`)
       — openai/xai use the OpenAI wire; anthropic the Messages wire; google the
       Gemini generateContent wire (``"gemini"``).
    """
    # E1 R2: ``:online`` is an OpenRouter web-search feature, not a native model
    # id. Stripping it and sending the remainder to xAI still cannot serve the
    # web-search contract — refuse BYOK interception so the call stays on
    # OpenRouter.
    if ":online" in model:
        return None

    provider, native_model = _resolve_model_prefix(model)
    if provider is None:
        return None
    if provider not in byok_enabled_providers():
        return None
    wire = _DIRECT_WIRE.get(provider)
    if wire is None:
        return None

    from core.routing.model_providers import PROVIDER_CONFIGS

    info = PROVIDER_CONFIGS.get(provider)
    if not info:
        return None
    api_key = os.getenv(info["env_var"], "")
    if not api_key:
        return None
    return BYOKTarget(provider, info["base_url"], api_key, native_model, wire)


def project_byok_env(enabled: dict[str, str]) -> None:
    """Project the enabled direct providers into the canonical (env) plane.

    ``enabled`` maps ``provider -> api_key`` for the direct providers the operator
    has turned on (see ``model_providers.enabled_direct_providers``). Writes the
    ``BYOK_DIRECT_PROVIDERS`` marker and ensures each provider's key env var is set
    so :func:`byok_target` resolves. Called at boot (from the persisted config) and
    on every PUT /providers/config — the coherent writer, mirroring how
    :func:`set_active_provider` keeps ``OLLAMA_ENABLED`` in step. An empty mapping
    clears the marker so a disable does not leave a stale route.
    """
    from core.routing.model_providers import PROVIDER_CONFIGS

    names = sorted(p.strip().lower() for p in enabled if p.strip())
    os.environ[_BYOK_MARKER] = ",".join(names)
    for provider, key in enabled.items():
        info = PROVIDER_CONFIGS.get(provider.strip().lower())
        if info and key:
            os.environ[info["env_var"]] = key
