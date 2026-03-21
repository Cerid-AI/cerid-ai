# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Provider management endpoints — BYOK provider listing and key validation."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
