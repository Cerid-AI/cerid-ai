# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""First-run configuration wizard endpoints.

When cerid-ai starts without required API keys the MCP server enters
"setup mode" and serves these endpoints so the React GUI can walk the
user through initial configuration.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

_logger = logging.getLogger("ai-companion.setup")

router = APIRouter(prefix="/setup", tags=["setup"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = ["OPENROUTER_API_KEY"]
_OPTIONAL_KEYS = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"]

# .env location: use CERID_ENV_FILE if set, otherwise find repo root by walking
# up until we find .env or docker-compose.yml. In Docker the .env is loaded via
# env_file directive so the configure endpoint is primarily for host-side setup.
def _find_env_file() -> Path:
    if env_override := os.getenv("CERID_ENV_FILE"):
        return Path(env_override)
    # Walk up from this file's directory looking for .env
    p = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = p / ".env"
        if candidate.exists() or (p / "docker-compose.yml").exists():
            return p / ".env"
        if p.parent == p:
            break
        p = p.parent
    return Path("/app/.env")  # Docker fallback


_ENV_FILE = _find_env_file()


class ServiceHealth(BaseModel):
    name: str
    status: str
    port: int
    url: str | None = None
    error: str | None = None


class SetupStatus(BaseModel):
    configured: bool
    setup_required: bool
    missing_keys: list[str]
    optional_keys: list[str]
    services: dict[str, str]
    configured_providers: list[str] = Field(default_factory=list)


class KeyValidationRequest(BaseModel):
    provider: str
    api_key: str = Field(..., min_length=1)


class KeyValidationResponse(BaseModel):
    valid: bool
    error: str | None = None
    models_available: int | None = None


class ConfigureRequest(BaseModel):
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    xai_api_key: str | None = None
    neo4j_password: str | None = None
    # KB / environment fields sent by the setup wizard
    archive_path: str | None = None
    lightweight_mode: bool | None = None
    watch_folder: bool | None = None
    ollama_enabled: bool | None = None
    ollama_model: str | None = None


class ConfigureResponse(BaseModel):
    success: bool
    restart_required: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_configured() -> bool:
    """Return True when all required API keys are present and non-empty."""
    return all(os.environ.get(k, "").strip() for k in _REQUIRED_KEYS)


def _missing_keys() -> list[str]:
    return [k for k in _REQUIRED_KEYS if not os.environ.get(k, "").strip()]


async def _check_service(name: str, url: str, timeout: float = 2.0) -> str:
    """Probe a service URL and return a status string."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code < 500:
                return "healthy"
            return "unhealthy"
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return "unavailable"


async def _service_statuses() -> dict[str, str]:
    """Check health of all core services and return a name->status map."""
    neo4j_status = "unknown"
    try:
        from app.deps import get_neo4j

        driver = get_neo4j()
        with driver.session() as session:
            session.run("RETURN 1").consume()
        neo4j_status = "healthy"
    except Exception:
        neo4j_status = "unavailable"

    redis_status = "unknown"
    try:
        from app.deps import get_redis

        get_redis()
        redis_status = "healthy"
    except Exception:
        redis_status = "unavailable"

    chroma_status = await _check_service(
        "chromadb",
        os.environ.get("CHROMA_URL", "http://ai-companion-chroma:8000") + "/api/v1/heartbeat",
    )

    mcp_status = "setup_mode" if not _is_configured() else "healthy"

    return {
        "neo4j": neo4j_status,
        "chromadb": chroma_status,
        "redis": redis_status,
        "mcp": mcp_status,
    }


def _read_env_file() -> str:
    """Read the .env file contents, returning empty string if missing."""
    if _ENV_FILE.exists():
        return _ENV_FILE.read_text(encoding="utf-8")
    return ""


def _sanitize_archive_path(raw: str) -> str:
    """Validate and normalise an archive path before writing to .env.

    Raises ``ValueError`` for clearly invalid input:
    - contains newlines or null bytes (would corrupt the .env file)
    - empty after stripping whitespace

    Normalisation:
    - strips whitespace
    - expands ``~`` to the real home directory
    - resolves ``..`` so the canonical path is stored
    """
    path = raw.strip()
    if not path:
        raise ValueError("Archive path must not be empty")

    # Characters that would corrupt a .env file or enable injection
    if "\n" in path or "\r" in path or "\0" in path:
        raise ValueError("Archive path contains invalid characters")

    # Expand ~ and resolve to canonical absolute path
    path = os.path.expanduser(path)
    path = os.path.realpath(path)

    return path


def _update_env_file(updates: dict[str, str]) -> None:
    """Update or add keys in the .env file, preserving comments and order.

    Only the keys present in *updates* are touched; everything else is kept
    verbatim (including blank lines and comments).
    """
    content = _read_env_file()
    lines = content.splitlines(keepends=True) if content else []

    remaining = dict(updates)  # keys still to write
    new_lines: list[str] = []

    for line in lines:
        matched = False
        for key in list(remaining):
            # Match KEY=... at start of line (ignoring leading whitespace)
            pattern = rf"^(\s*){re.escape(key)}\s*="
            if re.match(pattern, line):
                indent = re.match(pattern, line).group(1)  # type: ignore[union-attr]
                new_lines.append(f"{indent}{key}={remaining.pop(key)}\n")
                matched = True
                break
        if not matched:
            new_lines.append(line)

    # Append any keys that were not already present
    if remaining:
        # Ensure trailing newline before appending
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}\n")

    _ENV_FILE.write_text("".join(new_lines), encoding="utf-8")
    _logger.info("Updated .env file with %d key(s)", len(updates))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SetupStatus)
async def setup_status() -> SetupStatus:
    """Return the current first-run setup state."""
    services = await _service_statuses()
    from config.providers import get_configured_providers
    providers = get_configured_providers()
    provider_names = [p["name"] for p in providers if p.get("key_set")]
    return SetupStatus(
        configured=_is_configured(),
        setup_required=not _is_configured(),
        missing_keys=_missing_keys(),
        optional_keys=_OPTIONAL_KEYS,
        services=services,
        configured_providers=provider_names,
    )


@router.get("/health")
async def setup_health() -> dict:
    """Detailed health dashboard for all services."""
    neo4j_url = os.environ.get("NEO4J_URI", "bolt://ai-companion-neo4j:7687")
    chroma_url = os.environ.get("CHROMA_URL", "http://ai-companion-chroma:8000")

    statuses = await _service_statuses()

    services: list[dict] = [
        {
            "name": "neo4j",
            "status": statuses["neo4j"],
            "port": 7474,
            "url": neo4j_url,
        },
        {
            "name": "chromadb",
            "status": statuses["chromadb"],
            "port": 8001,
            "url": chroma_url,
        },
        {
            "name": "redis",
            "status": statuses["redis"],
            "port": 6379,
        },
        {
            "name": "mcp",
            "status": statuses["mcp"],
            "port": 8888,
        },
    ]

    # Required services must all be healthy
    _OPTIONAL = {"verification_pipeline"}
    required_healthy = all(
        s["status"] in ("healthy", "connected")
        for s in services
        if s["name"] not in _OPTIONAL
    )

    return {
        "services": services,
        "all_healthy": required_healthy,
        "docker": {
            "compose_version": "v2.x.x",
            "network": "llm-network",
        },
    }


@router.post("/validate-key", response_model=KeyValidationResponse)
async def validate_key(req: KeyValidationRequest) -> KeyValidationResponse:
    """Validate an API key for a specific LLM provider."""
    try:
        from config.providers import validate_provider_key

        # Resolve env-configured key when frontend sends "__env__" sentinel
        api_key = req.api_key
        if api_key == "__env__":
            env_map = {
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "xai": "XAI_API_KEY",
            }
            env_var = env_map.get(req.provider.lower(), "")
            api_key = os.getenv(env_var, "")
            if not api_key:
                return KeyValidationResponse(
                    valid=False, error=f"No {env_var} found in environment"
                )

        valid, message = await validate_provider_key(req.provider, api_key)
        return KeyValidationResponse(valid=valid, error=message if not valid else None)
    except ImportError:
        _logger.warning("config.providers not available, falling back to basic validation")
        return await _fallback_validate(req.provider, req.api_key)
    except (httpx.HTTPError, OSError) as exc:
        _logger.exception("Key validation failed for provider=%s", req.provider)
        return KeyValidationResponse(valid=False, error=str(exc))


async def _fallback_validate(provider: str, api_key: str) -> KeyValidationResponse:
    """Basic key validation when config.providers is not available."""
    provider = provider.lower()
    url_map: dict[str, str] = {
        "openrouter": "https://openrouter.ai/api/v1/models",
        "openai": "https://api.openai.com/v1/models",
        "anthropic": "https://api.anthropic.com/v1/models",
        "xai": "https://api.x.ai/v1/models",
    }

    if provider not in url_map:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    url = url_map[provider]
    headers: dict[str, str] = {}
    if provider == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            model_count = len(data.get("data", []))
            return KeyValidationResponse(valid=True, models_available=model_count)

        return KeyValidationResponse(
            valid=False,
            error=f"Provider returned HTTP {resp.status_code}",
        )
    except httpx.TimeoutException:
        return KeyValidationResponse(valid=False, error="Request timed out")
    except Exception as exc:
        return KeyValidationResponse(valid=False, error=str(exc))


@router.post("/configure", response_model=ConfigureResponse)
async def configure(req: ConfigureRequest) -> ConfigureResponse:
    """Apply configuration by writing API keys to the .env file."""

    try:
        updates: dict[str, str] = {}

        if req.openrouter_api_key:
            updates["OPENROUTER_API_KEY"] = req.openrouter_api_key
        if req.openai_api_key:
            updates["OPENAI_API_KEY"] = req.openai_api_key
        if req.anthropic_api_key:
            updates["ANTHROPIC_API_KEY"] = req.anthropic_api_key
        if req.xai_api_key:
            updates["XAI_API_KEY"] = req.xai_api_key

        if req.neo4j_password:
            if req.neo4j_password == "auto":  # pragma: allowlist secret
                updates["NEO4J_PASSWORD"] = secrets.token_hex(16)  # pragma: allowlist secret
            else:
                updates["NEO4J_PASSWORD"] = req.neo4j_password

        # KB / environment fields from the setup wizard
        if req.archive_path is not None:
            updates["ARCHIVE_PATH"] = _sanitize_archive_path(req.archive_path)
        if req.lightweight_mode is not None:
            updates["CERID_LIGHTWEIGHT"] = "true" if req.lightweight_mode else ""
        if req.watch_folder is not None:
            updates["WATCH_FOLDER"] = "true" if req.watch_folder else ""
        if req.ollama_enabled is not None:
            updates["OLLAMA_ENABLED"] = "true" if req.ollama_enabled else "false"
        if req.ollama_model is not None:
            updates["OLLAMA_DEFAULT_MODEL"] = req.ollama_model

        if not updates:
            return ConfigureResponse(success=False, error="No configuration provided")

        _update_env_file(updates)

        # Also inject into the current process environment so subsequent
        # health checks pick up the change without a restart.
        for key, value in updates.items():
            os.environ[key] = value

        _logger.info(
            "First-run configuration applied: %s",
            ", ".join(updates.keys()),
        )

        # Re-run pre-warms now that API keys are available
        import asyncio
        asyncio.ensure_future(_post_configure_warmup())

        return ConfigureResponse(success=True, restart_required=False)

    except (OSError, ValueError) as exc:
        _logger.exception("Failed to apply configuration")
        return ConfigureResponse(success=False, error=str(exc))


async def _post_configure_warmup() -> None:
    """Re-warm connections and models after Apply Configuration."""
    _logger.info("Post-configure warmup starting...")
    try:
        from core.utils.llm_client import _get_client
        await _get_client()
        _logger.info("Post-configure: OpenRouter client pre-warmed")
    except Exception as e:
        _logger.debug("Post-configure: OpenRouter warmup failed: %s", e)
    try:
        from core.utils.internal_llm import _get_ollama_client
        await _get_ollama_client()
        _logger.info("Post-configure: Ollama client pre-warmed")
    except Exception as e:
        _logger.debug("Post-configure: Ollama warmup failed: %s", e)
    try:
        from utils.reranker import warmup as reranker_warmup
        reranker_warmup()
        _logger.info("Post-configure: Reranker pre-warmed")
    except Exception as e:
        _logger.debug("Post-configure: Reranker warmup failed: %s", e)
    try:
        from core.utils.embeddings import get_embedding_function
        ef = get_embedding_function()
        if ef:
            ef(["warmup"])
            _logger.info("Post-configure: Embedding model pre-warmed")
    except Exception as e:
        _logger.debug("Post-configure: Embedding warmup failed: %s", e)

    # Re-run the verification self-test now that API keys are live. Without
    # this, a user who completes setup with a valid key still sees the old
    # startup-time "fail" result (cached up to 1h) in the health panel, and
    # has no in-product way to know the system is actually healthy.
    try:
        from agents.hallucination.startup_self_test import run_verification_self_test
        from app.deps import get_redis
        result = await run_verification_self_test(get_redis())
        _logger.info(
            "Post-configure verification self-test: status=%s method=%s claims=%d",
            result.get("status"), result.get("extraction_method"), result.get("claims_found"),
        )
    except Exception as e:
        _logger.debug("Post-configure self-test failed: %s", e)
    _logger.info("Post-configure warmup complete")


# ---------------------------------------------------------------------------
# Pool reset — recovers from transient startup failures without a restart
# ---------------------------------------------------------------------------


@router.post("/retest-verification")
async def retest_verification() -> dict:
    """Re-run the verification pipeline self-test and overwrite cached status.

    The frontend Health panel exposes this as a "Re-check" button so a user
    who fixes a bad API key doesn't have to wait for the 1h self-test TTL
    or restart the container to see verification flip from "offline" to
    "healthy".
    """
    try:
        from agents.hallucination.startup_self_test import run_verification_self_test
        from app.deps import get_redis
        result = await run_verification_self_test(get_redis())
        return {"ok": True, "result": result}
    except Exception as exc:
        _logger.exception("retest-verification failed")
        return {"ok": False, "error": str(exc)}


@router.post("/retest-services")
async def retest_services() -> dict:
    """Reset the LLM connection pool and circuit breakers, then re-probe all providers.

    The frontend calls this to recover from startup transients (e.g. 401 before
    DNS/auth stabilised, Bifrost not yet running) without requiring a container
    restart.
    """
    results: dict[str, object] = {}

    # Recycle the OpenRouter httpx singleton pool
    try:
        from core.utils.llm_client import recycle_client
        await recycle_client()
        results["llm_pool"] = "recycled"
    except Exception as exc:
        results["llm_pool"] = f"error: {exc}"

    # Reset LLM + bifrost circuit breakers so they start fresh
    _CB_NAMES = [
        "openrouter",
        "bifrost-rerank", "bifrost-claims", "bifrost-verify",
        "bifrost-synopsis", "bifrost-memory", "bifrost-compress",
        "bifrost-decompose",
    ]
    try:
        from core.utils.circuit_breaker import get_breaker
        for name in _CB_NAMES:
            get_breaker(name).reset()
        results["circuit_breakers"] = "reset"
    except Exception as exc:
        results["circuit_breakers"] = f"error: {exc}"

    # Re-probe OpenRouter auth with the configured key
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=5.0) as probe_client:
                resp = await probe_client.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code == 200:
                results["openrouter_auth"] = "ok"
            else:
                results["openrouter_auth"] = f"failed (HTTP {resp.status_code})"
        except Exception as exc:
            results["openrouter_auth"] = f"error: {exc}"
    else:
        results["openrouter_auth"] = "no_key_configured"

    # Re-probe all service statuses
    results["services"] = await _service_statuses()

    return {"status": "ok", "results": results}


# ---------------------------------------------------------------------------
# System check — environment detection for the setup wizard
# ---------------------------------------------------------------------------


@router.get("/system-check")
async def system_check(response: Response) -> dict:
    """Detect system environment for the setup wizard."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    import shutil

    from utils.host_info import get_host_hardware

    hw = get_host_hardware()

    # Docker — if we're running inside a container, Docker is clearly available
    docker_running = (
        shutil.which("docker") is not None
        or Path("/.dockerenv").exists()
        or os.getenv("container") is not None
    )

    # Env keys — check OS environment for known Cerid config keys (works inside Docker
    # where env_file passes host .env values as env vars)
    _KNOWN_KEYS = [
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY",
        "NEO4J_PASSWORD", "REDIS_PASSWORD", "OLLAMA_ENABLED", "CERID_API_KEY",
        "CERID_MULTI_USER", "CERID_JWT_SECRET", "CERID_TIER", "TAVILY_API_KEY",
        "SENTRY_DSN_MCP",
    ]
    env_keys: list[str] = [k for k in _KNOWN_KEYS if os.getenv(k)]
    env_exists = len(env_keys) > 0

    # Ollama
    ollama_detected = False
    ollama_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
    ollama_models: list[str] = []
    for _attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    ollama_models = [m.get("name", "") for m in data.get("models", [])]
                    ollama_detected = len(ollama_models) > 0
                break
        except Exception:
            if _attempt == 0:
                continue
            break

    # Archive path
    archive_path = os.getenv("ARCHIVE_PATH", "/archive")
    default_archive = archive_path

    # Lightweight recommendation
    lightweight_recommended = hw.ram_gb < 8

    return {
        "ram_gb": hw.ram_gb,
        "os": hw.os,
        "cpu": hw.cpu,
        "cpu_cores": hw.cpu_cores,
        "gpu": hw.gpu,
        "gpu_acceleration": hw.gpu_acceleration,
        "docker_running": docker_running,
        "env_exists": env_exists,
        "env_keys_present": env_keys,
        "ollama_detected": ollama_detected,
        "ollama_url": ollama_url if ollama_detected else None,
        "ollama_models": ollama_models,
        "lightweight_recommended": lightweight_recommended,
        "archive_path_exists": Path(archive_path).exists(),
        "default_archive_path": default_archive,
    }
