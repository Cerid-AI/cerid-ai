# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model provider configuration — manages API keys, failover, and routing.

Stored in Redis for persistence across restarts. Environment variables
take precedence over Redis config for backward compatibility.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("ai-companion.model_providers")

# ---------------------------------------------------------------------------
# Provider definitions with their direct API base URLs
# ---------------------------------------------------------------------------

PROVIDER_CONFIGS: dict[str, dict] = {
    "openrouter": {
        "display_name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_var": "OPENROUTER_API_KEY",
        "signup_url": "https://openrouter.ai/auth",
        "is_aggregator": True,  # Can route to any model
    },
    "openai": {
        "display_name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env_var": "OPENAI_API_KEY",
        "signup_url": "https://platform.openai.com/signup",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-5.4", "o1", "o3-mini"],
    },
    "anthropic": {
        "display_name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "env_var": "ANTHROPIC_API_KEY",
        "signup_url": "https://console.anthropic.com",
        "models": ["claude-sonnet-4.6", "claude-opus-4", "claude-haiku-3.5"],
    },
    "xai": {
        "display_name": "xAI",
        "base_url": "https://api.x.ai/v1",
        "env_var": "XAI_API_KEY",
        "signup_url": "https://console.x.ai",
        # Catalog-refreshed 2026-05-20: grok-4 + grok-4.1-fast removed
        # from OpenRouter. xAI direct API may still serve them under
        # legacy names, but for parity with the smart_router defaults we
        # advertise the current public lineup.
        "models": ["grok-4.20", "grok-4.20-multi-agent", "grok-4.3"],
    },
    "google": {
        "display_name": "Google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "env_var": "GOOGLE_API_KEY",
        "signup_url": "https://ai.google.dev",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
    },
    "ollama": {
        "display_name": "Ollama (Local)",
        "base_url": "http://localhost:11434",
        "env_var": "OLLAMA_URL",
        "signup_url": "https://ollama.com/download",
        "is_local": True,
    },
    # Quenchforge speaks the Ollama HTTP protocol on the same port shape, so
    # it appears as a separate local-backend option but flows through the same
    # wire format as Ollama. Selection is via INTERNAL_LLM_PROVIDER=quenchforge,
    # not its own enabled flag. Recommended on Intel Mac + AMD discrete GPU.
    "quenchforge": {
        "display_name": "Quenchforge (Local, Mac+AMD)",
        "base_url": "http://host.docker.internal:11434",
        "env_var": "QUENCHFORGE_URL",
        "signup_url": "https://github.com/cerid-ai/quenchforge",
        "is_local": True,
    },
}

# Map model prefixes to their native provider
MODEL_TO_PROVIDER: dict[str, str] = {
    "openai/": "openai",
    "anthropic/": "anthropic",
    "x-ai/": "xai",
    "google/": "google",
    "meta-llama/": "openrouter",  # Meta models only available via OpenRouter
}

_REDIS_KEY = "cerid:model_providers:config"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProviderState:
    enabled: bool = False
    api_key: str = ""
    is_default: bool = False
    url: str = ""  # For Ollama


@dataclass
class ModelProviderConfig:
    providers: dict[str, ProviderState] = field(default_factory=dict)
    model_overrides: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "providers": {k: asdict(v) for k, v in self.providers.items()},
            "model_overrides": self.model_overrides,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelProviderConfig:
        providers = {}
        for k, v in data.get("providers", {}).items():
            providers[k] = ProviderState(**v)
        return cls(
            providers=providers,
            model_overrides=data.get("model_overrides", {}),
        )


# ---------------------------------------------------------------------------
# Load / Save (synchronous Redis — matches deps.get_redis() signature)
# ---------------------------------------------------------------------------


def _overlay_env_keys(config: ModelProviderConfig) -> None:
    """E1 CR-097: overlay each direct provider's API key from its env var onto a
    Redis-loaded config. Env is the canonical key plane, so a rotation via
    .env/setup wins over the (key-stripped) Redis snapshot."""
    for name, info in PROVIDER_CONFIGS.items():
        if name in ("ollama", "quenchforge"):
            continue
        state = config.providers.get(name)
        if state is None:
            continue
        env_key = os.getenv(info["env_var"], "")
        if env_key:
            state.api_key = env_key


def load_config(redis_client) -> ModelProviderConfig:  # noqa: ANN001
    """Load provider config from Redis, falling back to env vars.

    E1 CR-097: env takes precedence for API keys. The Redis doc holds only
    structural state (keys are stripped on save); keys are overlaid from env on
    read, so a rotation via .env/setup is visible even after a PUT snapshot.
    """
    config = ModelProviderConfig()

    # Try Redis first (structural state), then overlay env keys (authoritative).
    if redis_client:
        try:
            raw = redis_client.get(_REDIS_KEY)
            if raw:
                config = ModelProviderConfig.from_dict(json.loads(raw))
                _overlay_env_keys(config)
                return config
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('core.routing.model_providers', exc)
            logger.debug("Failed to load model provider config from Redis", exc_info=True)

    # Fall back to env vars (backward compatibility)
    for name, info in PROVIDER_CONFIGS.items():
        env_var = info["env_var"]
        key_value = os.getenv(env_var, "")
        if name == "ollama":
            config.providers[name] = ProviderState(
                enabled=os.getenv("OLLAMA_ENABLED", "false").lower() == "true",
                url=key_value or "http://localhost:11434",
            )
        elif name == "quenchforge":
            # Quenchforge is opted in via INTERNAL_LLM_PROVIDER, not its own
            # env flag (it's mutually exclusive with the ollama provider slot —
            # both speak the same wire format).
            quenchforge_active = (
                os.getenv("INTERNAL_LLM_PROVIDER", "").lower() == "quenchforge"
            )
            config.providers[name] = ProviderState(
                enabled=quenchforge_active,
                url=key_value or "http://host.docker.internal:11434",
            )
        else:
            config.providers[name] = ProviderState(
                enabled=bool(key_value),
                api_key=key_value,
                is_default=(name == "openrouter"),
            )

    return config


def save_config(redis_client, config: ModelProviderConfig) -> None:  # noqa: ANN001
    """Save provider config to Redis.

    E1 CR-097: API keys are NEVER persisted to Redis — they live in the env
    plane (canonical). Only structural state (enabled/url/is_default/overrides)
    is stored; load_config overlays keys from env on read.
    """
    if redis_client:
        doc = config.to_dict()
        for pstate in doc.get("providers", {}).values():
            pstate["api_key"] = ""
        redis_client.set(_REDIS_KEY, json.dumps(doc))
        logger.info("Model provider config saved to Redis")


# ---------------------------------------------------------------------------
# Provider resolution / failover
# ---------------------------------------------------------------------------


def resolve_provider_for_model(
    model_id: str,
    config: ModelProviderConfig,
) -> tuple[str, str]:
    """Determine which provider to use for a given model.

    Returns ``(provider_name, api_key)``.

    Failover chain:
    1. Direct provider key if user has one for this model's provider
    2. OpenRouter if enabled (aggregator, can route any model)
    3. Free OpenRouter fallback for free-tier models
    4. ``("none", "")`` if nothing available
    """
    # Determine native provider from model ID. Tier ids carry the ``openrouter/``
    # prefix but MODEL_TO_PROVIDER keys are bare — strip it before matching or the
    # direct-key branch can never fire (E1 CR-008).
    native_id = model_id.removeprefix("openrouter/")
    native_provider: str | None = None
    for prefix, provider in MODEL_TO_PROVIDER.items():
        if native_id.startswith(prefix):
            native_provider = provider
            break

    # 1. Direct key for native provider?
    if native_provider and native_provider in config.providers:
        state = config.providers[native_provider]
        if state.enabled and state.api_key:
            return native_provider, state.api_key

    # 2. OpenRouter (aggregator)?
    or_state = config.providers.get("openrouter", ProviderState())
    if or_state.enabled and or_state.api_key:
        return "openrouter", or_state.api_key

    # 3. Free model via OpenRouter (even without explicit enable)?
    if "free" in model_id.lower() or ":free" in model_id:
        if or_state.api_key:  # Key exists but maybe disabled
            return "openrouter", or_state.api_key

    # 4. Nothing available
    return "none", ""


def enabled_direct_providers(config: ModelProviderConfig) -> dict[str, str]:
    """Return ``{provider: api_key}`` for the enabled DIRECT providers.

    Direct = neither the aggregator (OpenRouter) nor a local daemon
    (ollama/quenchforge) — derived from :data:`PROVIDER_CONFIGS` flags so the set
    stays single-sourced. Feeds ``provider_state.project_byok_env`` at boot and on
    PUT /providers/config, so the canonical env marker reflects the persisted
    BYOK enablement (E1 CR-008).
    """
    out: dict[str, str] = {}
    for name, state in config.providers.items():
        info = PROVIDER_CONFIGS.get(name, {})
        if info.get("is_aggregator") or info.get("is_local"):
            continue
        if state.enabled and state.api_key:
            out[name] = state.api_key
    return out


# ---------------------------------------------------------------------------
# Degraded mode detection
# ---------------------------------------------------------------------------


def get_degraded_status(config: ModelProviderConfig) -> dict:
    """Check if the system is in degraded mode and return status."""
    any_llm = False
    for name, state in config.providers.items():
        if name in ("ollama", "quenchforge"):
            # Local backends count as configured LLMs when their enabled flag
            # is set — they don't carry an api_key.
            if state.enabled:
                any_llm = True
        elif state.enabled and state.api_key:
            any_llm = True

    if not any_llm:
        return {
            "degraded": True,
            "reason": "No LLM provider configured",
            "affected": ["chat", "verification", "memory", "categorization"],
            "still_working": ["kb_search", "file_ingestion", "artifact_management"],
            "action": "Configure at least one provider in Settings \u2192 Advanced \u2192 Models",
        }

    return {"degraded": False}
