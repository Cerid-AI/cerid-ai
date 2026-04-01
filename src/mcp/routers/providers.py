# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Provider management endpoints — BYOK provider listing, key validation, and model config."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
from config.model_providers import (
    PROVIDER_CONFIGS,
    ProviderState,
    get_degraded_status,
    load_config,
    save_config,
)
from config.providers import (
    PROVIDER_REGISTRY,
    get_configured_providers,
    validate_provider_key,
)
from errors import ProviderError

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
    ollama_available = False
    try:
        import httpx
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=3)
        ollama_available = resp.status_code == 200
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Ollama availability check failed: %s", exc)

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


@router.get("/ollama/status")
async def get_ollama_status():
    """Check Ollama availability, installed models, and hardware info."""
    import httpx

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    enabled = os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1")
    result: dict = {
        "enabled": enabled,
        "url": ollama_url,
        "reachable": False,
        "models": [],
        "default_model": config.OLLAMA_DEFAULT_MODEL,
        "default_model_installed": False,
    }

    # Try configured URL first, then fall back to host.docker.internal
    # (handles native macOS Ollama accessed from inside Docker)
    urls_to_try = [ollama_url]
    if "host.docker.internal" not in ollama_url:
        urls_to_try.append("http://host.docker.internal:11434")

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            for url in urls_to_try:
                try:
                    resp = await client.get(f"{url}/api/tags")
                    if resp.status_code == 200:
                        result["reachable"] = True
                        result["url"] = url
                        models_data = resp.json().get("models", [])
                        result["models"] = [m.get("name", "") for m in models_data]
                        result["default_model_installed"] = any(
                            config.OLLAMA_DEFAULT_MODEL in m.get("name", "")
                            for m in models_data
                        )
                        # Persist the working URL for subsequent calls
                        if url != ollama_url:
                            os.environ["OLLAMA_URL"] = url
                            logger.info("Ollama found at %s (auto-detected)", url)
                        break
                except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                    continue
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Ollama status check failed: %s", exc)

    return result


@router.get("/ollama/recommendations")
async def get_ollama_recommendations():
    """Return model options with hardware-aware recommendation.

    Detects system RAM and suggests the best model for pipeline tasks.
    Models are USG-compliant (US/allied-origin only).
    """
    import asyncio
    import functools
    import platform
    import subprocess

    # Detect system RAM — offload blocking calls to thread pool
    ram_gb: float = 0.0
    cpu_info = ""
    gpu_info = ""
    system = platform.system()
    try:
        if system == "Darwin":
            raw = await asyncio.to_thread(
                functools.partial(subprocess.check_output, ["sysctl", "-n", "hw.memsize"], text=True),
            )
            ram_gb = int(raw.strip()) / (1024 ** 3)
            raw_cpu = await asyncio.to_thread(
                functools.partial(subprocess.check_output, ["sysctl", "-n", "machdep.cpu.brand_string"], text=True),
            )
            cpu_info = raw_cpu.strip()
            arch = platform.machine()
            gpu_info = "Apple Metal (unified)" if arch == "arm64" else "Intel (CPU only)"
        elif system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        ram_gb = int(line.split()[1]) / (1024 ** 2)
                        break
            raw_cpu = await asyncio.to_thread(
                functools.partial(subprocess.check_output, ["lscpu"], text=True, stderr=subprocess.DEVNULL),
            )
            for line in raw_cpu.splitlines():
                if "Model name" in line:
                    cpu_info = line.split(":", 1)[1].strip()
                    break
            # Check for NVIDIA GPU
            try:
                nv = await asyncio.to_thread(
                    functools.partial(
                        subprocess.check_output,
                        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                        text=True,
                    ),
                )
                gpu_info = f"NVIDIA {nv.strip()}" if nv.strip() else "CPU only"
            except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                gpu_info = "CPU only"
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Hardware detection failed: %s", exc)

    ram_gb = round(ram_gb, 1)

    # Model catalog — USG-compliant models only
    models: list[dict] = [
        {
            "id": "llama3.2:3b",
            "name": "Llama 3.2 3B",
            "origin": "Meta (US)",
            "size_gb": 2.0,
            "min_ram_gb": 8,
            "description": "Lightweight model for resource-constrained machines. Good at classification and extraction but slower on complex reasoning.",
            "strengths": "Smallest footprint, runs on any machine",
            "tier": "lightweight",
        },
        {
            "id": "llama3.1:8b",
            "name": "Llama 3.1 8B",
            "origin": "Meta (US)",
            "size_gb": 4.7,
            "min_ram_gb": 16,
            "description": "Balanced performance for pipeline tasks. Strong structured output, faster inference than 3B on capable hardware.",
            "strengths": "Best balance of speed, quality, and resource usage",
            "tier": "balanced",
        },
        {
            "id": "phi4:14b",
            "name": "Phi-4 14B",
            "origin": "Microsoft (US)",
            "size_gb": 9.1,
            "min_ram_gb": 32,
            "description": "High-quality reasoning and extraction. Excels at structured JSON output and claim analysis.",
            "strengths": "Best quality for complex extraction and reasoning tasks",
            "tier": "performance",
        },
    ]

    # Determine recommendation based on RAM (aligned with min_ram_gb tiers)
    if ram_gb >= 32:
        recommended = "phi4:14b"
    elif ram_gb >= 16:
        recommended = "llama3.1:8b"
    else:
        recommended = "llama3.2:3b"

    # Mark which models are compatible with this machine
    for m in models:
        min_ram: int = m["min_ram_gb"]  # type: ignore[assignment]
        m["compatible"] = ram_gb >= min_ram
        m["recommended"] = m["id"] == recommended

    return {
        "hardware": {
            "ram_gb": ram_gb,
            "cpu": cpu_info,
            "gpu": gpu_info,
            "platform": system,
        },
        "models": models,
        "recommended": recommended,
    }


class OllamaEnableRequest(BaseModel):
    model: str | None = Field(None, description="Override the default Ollama model")


@router.post("/ollama/enable")
async def enable_ollama(body: OllamaEnableRequest | None = None):
    """Enable Ollama as the internal LLM provider.

    Checks connectivity, updates runtime config to route pipeline
    intelligence calls to Ollama. Accepts optional model override.
    Does NOT persist to .env (that's done by start-cerid.sh or manually).
    """
    import httpx

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

    # Verify Ollama is reachable — try configured URL then host.docker.internal
    urls_to_try = [ollama_url]
    if "host.docker.internal" not in ollama_url:
        urls_to_try.append("http://host.docker.internal:11434")

    connected = False
    async with httpx.AsyncClient(timeout=5) as client:
        for url in urls_to_try:
            try:
                resp = await client.get(f"{url}/api/tags")
                if resp.status_code == 200:
                    ollama_url = url
                    if url != os.getenv("OLLAMA_URL", ""):
                        os.environ["OLLAMA_URL"] = url
                    connected = True
                    break
            except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                continue

    if not connected:
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to Ollama. Start with: open -a Ollama (macOS) or ollama serve (Linux)",
        )

    # Apply model override if provided
    selected_model = (body.model if body and body.model else None) or config.OLLAMA_DEFAULT_MODEL

    # Update runtime config
    config.INTERNAL_LLM_PROVIDER = "ollama"
    config.INTERNAL_LLM_MODEL = selected_model
    config.OLLAMA_DEFAULT_MODEL = selected_model

    # Update pipeline routing — route eligible stages to Ollama
    locked_stages = {"verification_complex", "chat_generation"}
    try:
        for stage in config.PIPELINE_PROVIDERS:
            if stage not in locked_stages:
                config.PIPELINE_PROVIDERS[stage] = "ollama"
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Failed to update pipeline routing to Ollama: %s", e)

    return {
        "status": "enabled",
        "provider": "ollama",
        "model": config.INTERNAL_LLM_MODEL,
        "url": ollama_url,
    }


@router.post("/ollama/disable")
async def disable_ollama():
    """Disable Ollama — fall back to Bifrost/OpenRouter for pipeline tasks."""
    config.INTERNAL_LLM_PROVIDER = "bifrost"
    # Revert pipeline routing to cloud
    try:
        for stage in config.PIPELINE_PROVIDERS:
            config.PIPELINE_PROVIDERS[stage] = "bifrost"
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Failed to revert pipeline routing to Bifrost: %s", e)
    return {"status": "disabled", "provider": "bifrost"}


@router.get("/credits")
async def get_provider_credits():
    """Get OpenRouter credit balance and usage stats."""
    import httpx

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"configured": False, "message": "No OpenRouter API key configured"}

    result: dict = {
        "configured": True,
        "provider": "openrouter",
        "top_up_url": "https://openrouter.ai/settings/credits",
        "account_url": "https://openrouter.ai/settings",
        "signup_url": "https://openrouter.ai/auth",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Get credits balance
            credits_resp = await client.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if credits_resp.status_code == 200:
                credits_data = credits_resp.json().get("data", {})
                total = credits_data.get("total_credits", 0)
                used = credits_data.get("total_usage", 0)
                result["balance"] = round(total - used, 2)
                result["total_credits"] = total
                result["total_usage"] = round(used, 2)

            # Get usage stats
            key_resp = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if key_resp.status_code == 200:
                key_data = key_resp.json().get("data", {})
                result["usage_daily"] = round(key_data.get("usage_daily", 0), 4)
                result["usage_weekly"] = round(key_data.get("usage_weekly", 0), 2)
                result["usage_monthly"] = round(key_data.get("usage_monthly", 0), 2)
                result["is_free_tier"] = key_data.get("is_free_tier", False)

                # Warning thresholds
                balance = result.get("balance", 0)
                if balance <= 0:
                    result["status"] = "exhausted"
                    result["warning"] = "Credits exhausted — add credits to continue using paid models"
                elif balance < 5:
                    result["status"] = "low"
                    result["warning"] = f"Low credits (${balance:.2f} remaining)"
                else:
                    result["status"] = "ok"
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("OpenRouter credit check failed: %s", e)
        result["error"] = str(e)
        result["status"] = "error"

    return result


@router.get("/routing")
async def get_routing_info():
    """Return smart routing configuration and current state."""
    from utils.smart_router import _check_ollama, _ollama_models, get_model_registry

    ollama_available = await _check_ollama()

    return {
        "ollama_available": ollama_available,
        "ollama_models": _ollama_models if ollama_available else [],
        "model_registry": get_model_registry(),
        "default_internal_model": os.getenv(
            "INTERNAL_LLM_MODEL", config.LLM_INTERNAL_MODEL
        ),
        "smart_routing_enabled": getattr(config, "SMART_ROUTING_ENABLED", True),
    }


@router.get("/config")
async def get_model_provider_config():
    """Get the full model provider configuration (keys masked)."""
    from deps import get_redis

    redis = get_redis()
    cfg = load_config(redis)
    result = cfg.to_dict()

    # Mask API keys in response — never send full key to frontend
    for name, state in result["providers"].items():
        if state.get("api_key"):
            key = state["api_key"]
            state["api_key_preview"] = (
                f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"
            )
            state["api_key_set"] = True
            del state["api_key"]  # Remove full key
        else:
            state["api_key_set"] = False

    # Add provider metadata for the frontend
    result["provider_info"] = {
        k: {
            "display_name": v["display_name"],
            "signup_url": v["signup_url"],
            "is_aggregator": v.get("is_aggregator", False),
            "is_local": v.get("is_local", False),
            "models": v.get("models", []),
        }
        for k, v in PROVIDER_CONFIGS.items()
    }

    result["degraded"] = get_degraded_status(cfg)

    return result


@router.put("/config")
async def update_model_provider_config(body: dict):
    """Update model provider configuration (persists to Redis)."""
    from deps import get_redis

    redis = get_redis()
    cfg = load_config(redis)

    # Update providers
    providers_update = body.get("providers", {})
    for pname, updates in providers_update.items():
        if pname not in cfg.providers:
            cfg.providers[pname] = ProviderState()
        state = cfg.providers[pname]

        if "enabled" in updates:
            state.enabled = updates["enabled"]
        if "api_key" in updates and updates["api_key"]:
            state.api_key = updates["api_key"]
            # Also write to env for backward compatibility with existing code paths
            env_var = PROVIDER_CONFIGS.get(pname, {}).get("env_var", "")
            if env_var:
                os.environ[env_var] = updates["api_key"]
        if "url" in updates:
            state.url = updates["url"]
        if "is_default" in updates:
            # Only one provider can be default
            if updates["is_default"]:
                for p in cfg.providers.values():
                    p.is_default = False
            state.is_default = updates["is_default"]

    # Update model overrides
    if "model_overrides" in body:
        cfg.model_overrides.update(body["model_overrides"])

    save_config(redis, cfg)

    return {"status": "updated"}


@router.get("/models/validate")
async def validate_model_registry():
    """Validate all active models against OpenRouter catalog."""
    from utils.model_registry import validate_models

    return await validate_models()


@router.get("/{name}")
async def get_provider(name: str):
    """Get details for a single provider including available models."""
    if name not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

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
    Key storage is handled separately via PUT /config.
    """
    if name not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

    logger.info("Validating key for provider: %s", name)
    valid, error = await validate_provider_key(name, req.api_key)

    return ValidateKeyResponse(
        valid=valid,
        error=error if not valid else None,
    )
