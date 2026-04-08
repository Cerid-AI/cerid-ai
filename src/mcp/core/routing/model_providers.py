# Copyright (c) 2026 Cerid AI. All rights reserved.
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
        "models": ["grok-4", "grok-4.1-fast"],
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


def load_config(redis_client) -> ModelProviderConfig:  # noqa: ANN001
    """Load provider config from Redis, falling back to env vars."""
    config = ModelProviderConfig()

    # Try Redis first
    if redis_client:
        try:
            raw = redis_client.get(_REDIS_KEY)
            if raw:
                config = ModelProviderConfig.from_dict(json.loads(raw))
                return config
        except Exception:
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
        else:
            config.providers[name] = ProviderState(
                enabled=bool(key_value),
                api_key=key_value,
                is_default=(name == "openrouter"),
            )

    return config


def save_config(redis_client, config: ModelProviderConfig) -> None:  # noqa: ANN001
    """Save provider config to Redis."""
    if redis_client:
        redis_client.set(_REDIS_KEY, json.dumps(config.to_dict()))
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
    # Determine native provider from model ID
    native_provider: str | None = None
    for prefix, provider in MODEL_TO_PROVIDER.items():
        if model_id.startswith(prefix):
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


# ---------------------------------------------------------------------------
# Degraded mode detection
# ---------------------------------------------------------------------------


def get_degraded_status(config: ModelProviderConfig) -> dict:
    """Check if the system is in degraded mode and return status."""
    any_llm = False
    for name, state in config.providers.items():
        if name == "ollama":
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
