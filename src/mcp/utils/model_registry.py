# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Dynamic model registry -- single source of truth for all model IDs and pricing.

Auto-validates against OpenRouter ``/api/v1/models`` on startup.
Graceful degradation: falls back to hardcoded defaults on network failure.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from errors import RoutingError

logger = logging.getLogger("ai-companion.model_registry")

ACTIVE_MODELS: dict[str, dict[str, Any]] = {
    "chat": {
        "default": "openrouter/openai/gpt-4o-mini",
        "advanced": "openrouter/anthropic/claude-sonnet-4.6",
        "frontier": "openrouter/anthropic/claude-opus-4.6",
        "reasoning": "openrouter/openai/o3-mini",
        "fast": "openrouter/x-ai/grok-4.1-fast",
    },
    "internal": {
        "default": "openrouter/openai/gpt-4o-mini",
        "free": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "pro": "openrouter/anthropic/claude-sonnet-4.6",
    },
    "verification": {
        "default": "openrouter/openai/gpt-4o-mini",
        "pool": [
            "openrouter/openai/gpt-4o-mini",
            "openrouter/google/gemini-2.5-flash",
            "openrouter/x-ai/grok-4.1-fast",
        ],
        "web_search": "openrouter/x-ai/grok-4.1-fast:online",
        "consistency": "openrouter/google/gemini-2.5-flash",
        "complex": "openrouter/google/gemini-2.5-flash",
    },
    "tiers": {
        "free": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "cheap": "openrouter/openai/gpt-4o-mini",
        "capable": "openrouter/anthropic/claude-sonnet-4.6",
        "research": "openrouter/x-ai/grok-4.1-fast",
        "expert": "openrouter/anthropic/claude-opus-4.6",
    },
}

_pricing_cache: dict[str, tuple[float, float]] = {}  # populated by validate_models
_FALLBACK_PRICING: dict[str, tuple[float, float]] = {  # per 1M tokens
    "openrouter/openai/gpt-4o-mini": (0.15, 0.60),
    "openrouter/anthropic/claude-sonnet-4.6": (3.0, 15.0),
    "openrouter/anthropic/claude-opus-4.6": (15.0, 75.0),
    "openrouter/google/gemini-2.5-flash": (0.15, 0.60),
    "openrouter/x-ai/grok-4.1-fast": (0.20, 0.50),
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
    "openrouter/openai/o3-mini": (1.10, 4.40),
}


def _strip_prefix(model_id: str) -> str:
    """Strip 'openrouter/' prefix for OpenRouter API lookup."""
    return model_id.removeprefix("openrouter/")


def _check_model(
    model_id: str, catalog: dict[str, dict],
    valid: list[str], invalid: list[str], pricing_updated: list[str], context: str,
) -> None:
    """Check a single model against the OpenRouter catalog."""
    # Strip openrouter/ prefix and :online/:free suffixes for catalog lookup
    lookup = _strip_prefix(model_id).split(":")[0]
    entry = catalog.get(lookup)
    if entry is None:
        invalid.append(f"{model_id} ({context})")
        return
    valid.append(model_id)
    pricing = entry.get("pricing", {})
    new = (float(pricing.get("prompt", 0)) * 1e6, float(pricing.get("completion", 0)) * 1e6)
    old = _pricing_cache.get(model_id)
    _pricing_cache[model_id] = new
    if old and old != new:
        pricing_updated.append(model_id)


def _iter_all_models() -> list[tuple[str, str]]:
    """Return (model_id, context_label) pairs for every model in ACTIVE_MODELS."""
    result: list[tuple[str, str]] = []
    for role, models in ACTIVE_MODELS.items():
        if not isinstance(models, dict):
            continue
        for key, val in models.items():
            if isinstance(val, list):
                result.extend((m, f"{role}.{key}") for m in val)
            elif isinstance(val, str):
                result.append((val, f"{role}.{key}"))
    return result


async def validate_models() -> dict[str, Any]:
    """Validate all active models against the OpenRouter catalog."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            catalog = {m["id"]: m for m in resp.json().get("data", [])}

        valid: list[str] = []
        invalid: list[str] = []
        pricing_updated: list[str] = []

        for model_id, context in _iter_all_models():
            _check_model(model_id, catalog, valid, invalid, pricing_updated, context)

        if invalid:
            logger.warning("Invalid/deprecated models detected: %s", invalid)

        return {
            "valid": valid,
            "invalid": invalid,
            "pricing_updated": pricing_updated,
            "catalog_size": len(catalog),
        }
    except (RoutingError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Model validation failed: %s", exc)
        return {"valid": [], "invalid": [], "pricing_updated": [], "error": str(exc)}


def get_model(role: str, key: str = "default") -> str:
    """Get a model ID from the registry by role and key."""
    role_models = ACTIVE_MODELS.get(role, {})
    if isinstance(role_models, dict):
        value = role_models.get(key, role_models.get("default", "openrouter/openai/gpt-4o-mini"))
        if isinstance(value, str):
            return value
        return role_models.get("default", "openrouter/openai/gpt-4o-mini")
    return "openrouter/openai/gpt-4o-mini"


def get_pricing(model_id: str) -> tuple[float, float]:
    """Return (input_cost_per_1M, output_cost_per_1M) for a model."""
    if model_id in _pricing_cache:
        return _pricing_cache[model_id]
    return _FALLBACK_PRICING.get(model_id, (0.0, 0.0))


async def fetch_and_compare_models() -> dict[str, Any]:
    """Fetch current OpenRouter catalog, compare with cached version, store updates in Redis."""
    import json as _json

    from core.utils.time import utcnow_iso
    from deps import get_redis

    redis = get_redis()
    catalog_key = "cerid:models:catalog"
    updates_key = "cerid:models:updates"

    # Fetch current catalog from OpenRouter
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get("https://openrouter.ai/api/v1/models")
        resp.raise_for_status()
        current_models = {m["id"]: m for m in resp.json().get("data", [])}

    # Load previous catalog from Redis
    previous_ids: set[str] = set()
    raw_prev = redis.get(catalog_key)
    if raw_prev:
        try:
            previous_ids = set(_json.loads(raw_prev))
        except (ValueError, TypeError):
            pass

    current_ids = set(current_models.keys())
    new_ids = current_ids - previous_ids
    deprecated_ids = previous_ids - current_ids

    # Build detailed lists
    new_models = []
    for mid in sorted(new_ids):
        m = current_models[mid]
        new_models.append({
            "id": mid,
            "name": m.get("name", mid),
            "context_length": m.get("context_length"),
            "pricing": m.get("pricing", {}),
        })

    deprecated_models = [{"id": mid} for mid in sorted(deprecated_ids)]

    # Store updated catalog (just the ID list — lightweight)
    redis.set(catalog_key, _json.dumps(sorted(current_ids)))

    # Store the diff
    updates = {
        "new": new_models,
        "deprecated": deprecated_models,
        "last_checked": utcnow_iso(),
        "catalog_size": len(current_ids),
    }
    redis.set(updates_key, _json.dumps(updates))

    return updates


__all__ = [
    "ACTIVE_MODELS",
    "get_model",
    "get_pricing",
    "validate_models",
    "fetch_and_compare_models",
]
