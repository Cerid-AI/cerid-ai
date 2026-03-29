# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""BYOK provider registry — supported LLM providers, key validation, and status."""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("ai-companion.providers")

# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------
# Each entry defines a supported LLM provider with connection details,
# validation endpoint, and default model catalog.

PROVIDER_REGISTRY: dict[str, dict] = {
    "openrouter": {
        "name": "openrouter",
        "display_name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_var": "OPENROUTER_API_KEY",
        "test_endpoint": "/models",
        "requires_api_key": True,
        "models": [
            "openrouter/openai/gpt-4o-mini",
            "openrouter/anthropic/claude-sonnet-4.6",
            "openrouter/google/gemini-2.5-flash",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "openrouter/x-ai/grok-4.1-fast",
        ],
    },
    "openai": {
        "name": "openai",
        "display_name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env_var": "OPENAI_API_KEY",
        "test_endpoint": "/models",
        "requires_api_key": True,
        "models": [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
        ],
    },
    "anthropic": {
        "name": "anthropic",
        "display_name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "env_var": "ANTHROPIC_API_KEY",
        "test_endpoint": "/messages",
        "requires_api_key": True,
        "models": [
            "claude-sonnet-4-20250514",
            "claude-haiku-4-20250514",
            "claude-opus-4-20250514",
        ],
    },
    "xai": {
        "name": "xai",
        "display_name": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "env_var": "XAI_API_KEY",
        "test_endpoint": "/models",
        "requires_api_key": True,
        "models": [
            "grok-4",
            "grok-4-mini",
            "grok-4.1-fast",
        ],
    },
    "ollama": {
        "name": "ollama",
        "display_name": "Ollama (Local)",
        "base_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
        "env_var": "OLLAMA_URL",
        "test_endpoint": "/api/tags",
        "requires_api_key": False,
        "models": [
            "llama3.2",
            "llama3.3",
            "mistral",
            "codellama",
            "gemma2",
            "phi3",
        ],
    },
}

# Shared httpx timeout for validation calls
_VALIDATION_TIMEOUT = 10.0


async def validate_provider_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Validate an API key by making a test call to the provider.

    Returns (True, "OK") on success, or (False, "error message") on failure.
    """
    if provider not in PROVIDER_REGISTRY:
        return False, f"Unknown provider: {provider}"

    entry = PROVIDER_REGISTRY[provider]

    if entry["requires_api_key"] and not api_key:
        return False, "API key is required"

    try:
        async with httpx.AsyncClient(timeout=_VALIDATION_TIMEOUT) as client:
            if provider == "anthropic":
                # Anthropic uses POST with x-api-key header and requires a body
                resp = await client.post(
                    f"{entry['base_url']}{entry['test_endpoint']}",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-20250514",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "Hi"}],
                    },
                )
            elif provider == "ollama":
                # Ollama runs locally, no auth needed
                resp = await client.get(
                    f"{entry['base_url']}{entry['test_endpoint']}",
                )
            else:
                # OpenRouter, OpenAI, xAI all use GET with Bearer token
                resp = await client.get(
                    f"{entry['base_url']}{entry['test_endpoint']}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )

            if resp.status_code in (200, 201):
                logger.info("Provider %s key validation succeeded", provider)
                return True, "OK"

            # Parse error detail from response body if possible
            try:
                body = resp.json()
                detail = body.get("error", {}).get("message", "") or body.get("detail", "")
            except Exception:
                detail = resp.text[:200]

            msg = f"HTTP {resp.status_code}: {detail}" if detail else f"HTTP {resp.status_code}"
            logger.warning("Provider %s key validation failed: %s", provider, msg)
            return False, msg

    except httpx.ConnectError:
        msg = f"Connection failed — is {entry['display_name']} reachable?"
        if provider == "ollama":
            msg = "Connection failed — is Ollama running locally? (ollama serve)"
        logger.warning("Provider %s validation connect error", provider)
        return False, msg
    except httpx.TimeoutException:
        msg = f"Request timed out after {_VALIDATION_TIMEOUT}s"
        logger.warning("Provider %s validation timeout", provider)
        return False, msg
    except Exception as exc:
        logger.error("Provider %s validation unexpected error: %s", provider, exc)
        return False, f"Unexpected error: {exc}"


def get_configured_providers() -> list[dict]:
    """Return providers that have API keys set in the environment.

    Each entry includes the provider metadata plus:
    - ``key_set``: whether the env var has a value
    - ``key_preview``: masked key preview (first 4 + last 4 chars)
    """
    results = []
    for name, entry in PROVIDER_REGISTRY.items():
        env_var = entry["env_var"]
        if not entry["requires_api_key"]:
            # Local providers (ollama) are always "configured"
            results.append({
                "name": entry["name"],
                "display_name": entry["display_name"],
                "requires_api_key": False,
                "key_set": True,
                "key_preview": None,
                "models": entry["models"],
            })
            continue

        api_key = os.getenv(env_var, "")
        if api_key:
            # Mask key: show first 4 and last 4 chars
            if len(api_key) > 8:
                preview = f"{api_key[:4]}...{api_key[-4:]}"
            else:
                preview = "****"
            results.append({
                "name": entry["name"],
                "display_name": entry["display_name"],
                "requires_api_key": True,
                "key_set": True,
                "key_preview": preview,
                "models": entry["models"],
            })

    return results
