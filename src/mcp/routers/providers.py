# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Provider management endpoints — BYOK provider listing and key validation."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
from config.providers import (
    PROVIDER_REGISTRY,
    get_configured_providers,
    validate_provider_key,
)

router = APIRouter(prefix="/providers", tags=["providers"])
logger = logging.getLogger("ai-companion.providers")


# ── Pydantic models ──────────────────────────────────────────────────────────


class ValidateKeyRequest(BaseModel):
    api_key: str = Field(..., description="API key to validate against the provider")


class ValidateKeyResponse(BaseModel):
    valid: bool
    error: str | None = None


class ProviderInfo(BaseModel):
    name: str
    display_name: str
    base_url: str
    requires_api_key: bool
    key_set: bool
    key_preview: str | None = None
    models: list[str]


class ProviderListResponse(BaseModel):
    providers: list[ProviderInfo]
    total: int


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("", response_model=ProviderListResponse)
async def list_providers():
    """List all supported providers with their connection status."""
    import os

    providers = []
    for name, entry in PROVIDER_REGISTRY.items():
        env_var = entry["env_var"]
        api_key = os.getenv(env_var, "") if env_var else ""
        key_set = bool(api_key) or not entry["requires_api_key"]

        # Mask key preview
        key_preview = None
        if api_key and len(api_key) > 8:
            key_preview = f"{api_key[:4]}...{api_key[-4:]}"
        elif api_key:
            key_preview = "****"

        providers.append(ProviderInfo(
            name=entry["name"],
            display_name=entry["display_name"],
            base_url=entry["base_url"],
            requires_api_key=entry["requires_api_key"],
            key_set=key_set,
            key_preview=key_preview,
            models=entry["models"],
        ))

    return ProviderListResponse(providers=providers, total=len(providers))


@router.get("/configured")
async def list_configured_providers():
    """Return only providers that have API keys configured."""
    configured = get_configured_providers()
    return {"providers": configured, "total": len(configured)}


@router.get("/internal")
async def get_internal_provider():
    """Return the configured internal LLM provider for pipeline operations."""
    import os

    ollama_available = False
    try:
        import httpx
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=3)
        ollama_available = resp.status_code == 200
    except Exception:
        pass

    return {
        "provider": getattr(config, "INTERNAL_LLM_PROVIDER", "bifrost"),
        "model": getattr(config, "INTERNAL_LLM_MODEL", ""),
        "intelligence_model": getattr(config, "INTELLIGENCE_MODEL", ""),
        "ollama_available": ollama_available,
    }


@router.put("/internal")
async def set_internal_provider(body: dict):
    """Update internal LLM provider configuration (runtime, not persisted to .env)."""
    provider = body.get("provider", "bifrost")
    model = body.get("model", "")
    intelligence_model = body.get("intelligence_model", "")

    if provider not in ("bifrost", "ollama"):
        raise HTTPException(status_code=400, detail="Provider must be 'bifrost' or 'ollama'")

    config.INTERNAL_LLM_PROVIDER = provider
    config.INTERNAL_LLM_MODEL = model
    if intelligence_model:
        config.INTELLIGENCE_MODEL = intelligence_model

    return {"status": "updated", "provider": provider, "model": model}


@router.get("/{name}")
async def get_provider(name: str):
    """Get details for a single provider including available models."""
    if name not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

    import os

    entry = PROVIDER_REGISTRY[name]
    env_var = entry["env_var"]
    api_key = os.getenv(env_var, "") if env_var else ""
    key_set = bool(api_key) or not entry["requires_api_key"]

    key_preview = None
    if api_key and len(api_key) > 8:
        key_preview = f"{api_key[:4]}...{api_key[-4:]}"
    elif api_key:
        key_preview = "****"

    return {
        "name": entry["name"],
        "display_name": entry["display_name"],
        "base_url": entry["base_url"],
        "env_var": env_var,
        "requires_api_key": entry["requires_api_key"],
        "key_set": key_set,
        "key_preview": key_preview,
        "models": entry["models"],
        "test_endpoint": entry["test_endpoint"],
    }


@router.post("/{name}/validate", response_model=ValidateKeyResponse)
async def validate_key(name: str, req: ValidateKeyRequest):
    """Validate an API key against a provider's test endpoint.

    Makes a real HTTP call to the provider to verify the key is valid.
    Does NOT store the key — that's a future sprint.
    """
    if name not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

    logger.info("Validating key for provider: %s", name)
    valid, error = await validate_provider_key(name, req.api_key)

    return ValidateKeyResponse(
        valid=valid,
        error=error if not valid else None,
    )


# ── Internal LLM provider endpoints ─────────────────────────────────────────


def _check_ollama_available() -> bool:
    """Quick check if Ollama is reachable."""
    import os

    try:
        import httpx

        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


@router.get("/internal")
async def get_internal_provider():
    """Return the configured internal LLM provider for pipeline operations."""
    return {
        "provider": getattr(config, "INTERNAL_LLM_PROVIDER", "bifrost"),
        "model": getattr(config, "INTERNAL_LLM_MODEL", ""),
        "intelligence_model": getattr(config, "INTELLIGENCE_MODEL", ""),
        "ollama_available": _check_ollama_available(),
    }


@router.put("/internal")
async def set_internal_provider(body: dict):
    """Update internal LLM provider configuration (runtime only, not persisted to .env)."""
    provider = body.get("provider", "bifrost")
    model = body.get("model", "")
    intelligence_model = body.get("intelligence_model", "")

    if provider not in ("bifrost", "ollama"):
        raise HTTPException(status_code=400, detail="Provider must be 'bifrost' or 'ollama'")

    # Update runtime config
    config.INTERNAL_LLM_PROVIDER = provider
    config.INTERNAL_LLM_MODEL = model
    if intelligence_model:
        config.INTELLIGENCE_MODEL = intelligence_model

    return {"status": "updated", "provider": provider, "model": model}
