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
import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from errors import ConfigError

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


class KeyValidationRequest(BaseModel):
    provider: str
    api_key: str = Field(..., min_length=1)


class KeyValidationResponse(BaseModel):
    valid: bool
    error: str | None = None
    models_available: int | None = None


class SystemCheckResponse(BaseModel):
    ram_gb: float
    docker_running: bool
    env_exists: bool
    env_keys_present: list[str]
    ollama_detected: bool
    ollama_url: str | None = None
    ollama_models: list[str] = Field(default_factory=list)
    lightweight_recommended: bool
    archive_path_exists: bool
    default_archive_path: str


class ConfigureRequest(BaseModel):
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    xai_api_key: str | None = None
    neo4j_password: str | None = None
    # New fields
    archive_path: str | None = None
    domains: list[str] | None = None
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
        from deps import get_neo4j

        driver = get_neo4j()
        with driver.session() as session:
            session.run("RETURN 1").consume()
        neo4j_status = "healthy"
    except (ConfigError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        neo4j_status = "unavailable"

    redis_status = "unknown"
    try:
        from deps import get_redis

        get_redis()
        redis_status = "healthy"
    except (ConfigError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        redis_status = "unavailable"

    chroma_status = await _check_service(
        "chromadb",
        os.environ.get("CHROMA_URL", "http://ai-companion-chroma:8000") + "/api/v1/heartbeat",
    )

    bifrost_url = os.environ.get("BIFROST_URL", "http://ai-companion-bifrost:8080")
    if not _is_configured():
        bifrost_status = "unavailable"
    else:
        bifrost_status = await _check_service("bifrost", f"{bifrost_url}/health")

    mcp_status = "setup_mode" if not _is_configured() else "healthy"

    return {
        "neo4j": neo4j_status,
        "chromadb": chroma_status,
        "redis": redis_status,
        "bifrost": bifrost_status,
        "mcp": mcp_status,
    }


def _read_env_file() -> str:
    """Read the .env file contents, returning empty string if missing."""
    if _ENV_FILE.exists():
        return _ENV_FILE.read_text(encoding="utf-8")
    return ""


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
    return SetupStatus(
        configured=_is_configured(),
        setup_required=not _is_configured(),
        missing_keys=_missing_keys(),
        optional_keys=_OPTIONAL_KEYS,
        services=services,
    )


@router.get("/system-check", response_model=SystemCheckResponse)
async def system_check() -> SystemCheckResponse:
    """Detect host capabilities for the setup wizard."""
    # RAM detection — prefer /proc/meminfo (reports host RAM even inside Docker)
    # because os.sysconf reports cgroup-limited memory in containers.
    ram_gb = 0.0
    try:
        meminfo = Path("/proc/meminfo").read_text()
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                ram_kb = int(line.split()[1])
                ram_gb = ram_kb / (1024**2)
                break
    except (OSError, ValueError):
        pass
    if ram_gb == 0.0:
        try:
            ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
        except OSError:
            pass

    # Docker detection: running inside a container, socket mounted, or binary on PATH
    docker_running = (
        Path("/.dockerenv").exists()
        or Path("/var/run/docker.sock").exists()
        or shutil.which("docker") is not None
    )

    # .env file scan
    env_exists = _ENV_FILE.exists()
    env_keys_present: list[str] = []
    if env_exists:
        content = _read_env_file()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, _, value = stripped.partition("=")
                if value.strip():
                    env_keys_present.append(key.strip())

    # Ollama detection
    ollama_detected = False
    ollama_url: str | None = None
    ollama_models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                ollama_detected = True
                ollama_url = "http://localhost:11434"
                data = resp.json()
                ollama_models = [m["name"] for m in data.get("models", [])]
    except (httpx.ConnectError, httpx.TimeoutException, OSError, KeyError, ValueError):
        pass  # Ollama not running — detected stays False

    # Archive path
    default_archive_path = str(Path.home() / "cerid-archive")
    archive_path_exists = Path(default_archive_path).exists()

    return SystemCheckResponse(
        ram_gb=round(ram_gb, 1),
        docker_running=docker_running,
        env_exists=env_exists,
        env_keys_present=env_keys_present,
        ollama_detected=ollama_detected,
        ollama_url=ollama_url,
        ollama_models=ollama_models,
        lightweight_recommended=ram_gb < 12,
        archive_path_exists=archive_path_exists,
        default_archive_path=default_archive_path,
    )


@router.get("/health")
async def setup_health() -> dict:
    """Detailed health dashboard for all services."""
    neo4j_url = os.environ.get("NEO4J_URI", "bolt://ai-companion-neo4j:7687")
    chroma_url = os.environ.get("CHROMA_URL", "http://ai-companion-chroma:8000")
    bifrost_url = os.environ.get("BIFROST_URL", "http://ai-companion-bifrost:8080")

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
            "name": "bifrost",
            "status": statuses["bifrost"],
            "port": 8080,
            "url": bifrost_url,
            **({"error": "OPENROUTER_API_KEY not set"} if not _is_configured() else {}),
        },
        {
            "name": "mcp",
            "status": statuses["mcp"],
            "port": 8888,
        },
    ]

    # Add verification pipeline status from Redis self-test result
    try:
        from agents.hallucination.startup_self_test import get_self_test_status_sync
        from deps import get_redis
        _redis = get_redis()
        vp_result = get_self_test_status_sync(_redis)
        vp_status = "healthy" if vp_result and vp_result.get("status") == "pass" else "unavailable"
        services.append({"name": "verification_pipeline", "status": vp_status, "port": 0})
    except (ConfigError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        services.append({"name": "verification_pipeline", "status": "unavailable", "port": 0})

    all_healthy = all(s["status"] in ("healthy", "connected") for s in services)
    return {
        "all_healthy": all_healthy,
        "services": services,
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

        valid, message = await validate_provider_key(req.provider, req.api_key)
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
    except (ConfigError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        return KeyValidationResponse(valid=False, error=str(exc))


@router.post("/configure", response_model=ConfigureResponse)
async def configure(req: ConfigureRequest) -> ConfigureResponse:
    """Apply first-run configuration by writing API keys to the .env file."""
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
            if req.neo4j_password == "auto":
                updates["NEO4J_PASSWORD"] = secrets.token_hex(16)  # pragma: allowlist secret
            else:
                updates["NEO4J_PASSWORD"] = req.neo4j_password

        # Handle new configuration fields
        if req.archive_path:
            updates["WATCH_FOLDER"] = req.archive_path
        if req.domains is not None:
            updates["CERID_ACTIVE_DOMAINS"] = ",".join(req.domains)
        if req.lightweight_mode is not None:
            updates["CERID_LIGHTWEIGHT"] = str(req.lightweight_mode).lower()
        if req.watch_folder is not None:
            updates["CERID_WATCH_ENABLED"] = str(req.watch_folder).lower()
        if req.ollama_enabled is not None:
            updates["OLLAMA_ENABLED"] = str(req.ollama_enabled).lower()
        if req.ollama_model:
            updates["OLLAMA_DEFAULT_MODEL"] = req.ollama_model

        # Auto-generate infrastructure passwords if not already set
        if not os.environ.get("NEO4J_PASSWORD", "").strip():
            updates.setdefault("NEO4J_PASSWORD", secrets.token_hex(16))
        if not os.environ.get("REDIS_PASSWORD", "").strip():
            updates["REDIS_PASSWORD"] = secrets.token_hex(16)

        if not updates:
            return ConfigureResponse(success=False, error="No keys provided")

        _update_env_file(updates)

        # Also inject into the current process environment so subsequent
        # health checks pick up the change without a restart.
        for key, value in updates.items():
            os.environ[key] = value

        # Create archive directory + domain subdirectories if configured
        if req.archive_path:
            archive = Path(req.archive_path).expanduser()
            archive.mkdir(parents=True, exist_ok=True)
            if req.domains:
                for domain in req.domains:
                    (archive / domain).mkdir(exist_ok=True)

        _logger.info(
            "First-run configuration applied: %s",
            ", ".join(updates.keys()),
        )

        return ConfigureResponse(success=True, restart_required=True)

    except (OSError, ValueError) as exc:
        _logger.exception("Failed to apply configuration")
        return ConfigureResponse(success=False, error=str(exc))
