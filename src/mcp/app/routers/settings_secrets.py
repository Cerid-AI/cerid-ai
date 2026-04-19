# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Write-only secrets management endpoints.

These endpoints provide a three-part contract for updating sensitive API keys
without ever returning the raw value in any response body:

  GET  /settings/openrouter-key        -> {configured, last4, updated_at}
  PUT  /settings/openrouter-key        -> same shape; stores key server-side
  POST /settings/openrouter-key/test   -> {valid, credits_remaining, error}

The existing POST /setup/configure wizard endpoint remains unchanged.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

_logger = logging.getLogger("ai-companion.settings_secrets")

router = APIRouter(tags=["settings-secrets"])


def _scrub_input(errors: Sequence) -> list:
    """Return a copy of Pydantic validation errors with 'input' and 'ctx' fields
    removed.

    - 'input' contains the raw user-supplied value (R4-1 invariant: must never
      appear in a response body).
    - 'ctx' carries the Python exception object (e.g. ValueError), which is not
      JSON-serialisable and which can include exception message text that might
      hint at the value.

    Used by the R4-1-safe validation error handler below.
    """
    scrubbed = []
    for err in errors:
        e = {k: v for k, v in err.items() if k not in ("input", "ctx")}
        scrubbed.append(e)
    return scrubbed


def register_redacted_validation_handler(app: FastAPI) -> None:
    """Register a 422 exception handler that strips 'input' from all error
    details.  Call this on any FastAPI app that includes the settings_secrets
    router so the R4-1 invariant holds: a mis-typed key never echoes in the
    response body.
    """

    @app.exception_handler(RequestValidationError)
    async def _redacted_422(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": _scrub_input(exc.errors())},
        )


# ---------------------------------------------------------------------------
# .env.meta.json — sidecar that tracks when each key was last updated
# Lives next to .env in the repo root.
# ---------------------------------------------------------------------------

def _env_file_dir() -> Path:
    """Return the directory containing the .env file (same as setup.py logic)."""
    if env_override := os.getenv("CERID_ENV_FILE"):
        return Path(env_override).parent
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / ".env").exists() or (p / "docker-compose.yml").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return Path("/app")


def _meta_path() -> Path:
    return _env_file_dir() / ".env.meta.json"


def _load_meta() -> dict[str, str]:
    """Load the sidecar metadata file; returns empty dict if missing/corrupt."""
    path = _meta_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_meta(meta: dict[str, str]) -> None:
    """Persist the sidecar metadata file."""
    try:
        _meta_path().write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError as exc:
        _logger.warning("Failed to write .env.meta.json: %s", exc)


def _get_key_updated_at(var_name: str) -> str | None:
    """Return the ISO 8601 timestamp when *var_name* was last updated, or None."""
    return _load_meta().get(var_name)


def _set_key_updated_at(var_name: str) -> None:
    """Record that *var_name* was just updated (now, in UTC)."""
    meta = _load_meta()
    meta[var_name] = datetime.now(timezone.utc).isoformat()
    _save_meta(meta)


# ---------------------------------------------------------------------------
# Re-use _update_env_file from setup.py to stay DRY
# ---------------------------------------------------------------------------

def _update_env_file(updates: dict[str, str]) -> None:
    """Delegate to setup.py's _update_env_file helper."""
    from app.routers.setup import _update_env_file as _setup_update_env_file
    _setup_update_env_file(updates)


# ---------------------------------------------------------------------------
# Pydantic models — security invariant: no field may carry the raw key value
# ---------------------------------------------------------------------------


class OpenRouterKeyStatusResponse(BaseModel):
    """Status-only response — the raw key value is NEVER included."""

    configured: bool
    last4: str | None = None
    updated_at: str | None = None  # ISO 8601 or None if never set


# Minimum length guard — enforced via custom validator rather than
# Field(min_length=...) because the declarative constraint produces a
# FastAPI 422 error whose `input` field echoes the user-supplied value
# back. That would violate the R4-1 invariant "raw key never appears
# in any response body." A custom validator raising ValueError produces
# a response with only the generic message.
_MIN_KEY_LENGTH = 16  # OpenRouter keys are 40+ chars; 16 is a safe floor


class OpenRouterKeyPutRequest(BaseModel):
    # hide_input_in_errors suppresses the `input` field from Pydantic v2
    # ValidationError serialisation, ensuring the user-supplied value never
    # appears in a FastAPI 422 response body (R4-1 invariant).
    model_config = ConfigDict(hide_input_in_errors=True)

    api_key: str = Field(..., description="OpenRouter API key (write-only)")

    @field_validator("api_key")
    @classmethod
    def _validate_length(cls, v: str) -> str:
        # Deliberately generic message — do NOT include v, its length, or any
        # hint of the input. The user-facing string must carry zero signal
        # that could aid extracting the plaintext via error differentials.
        if len(v) < _MIN_KEY_LENGTH:
            raise ValueError("API key too short")
        return v


class OpenRouterKeyTestRequest(BaseModel):
    api_key: str | None = None  # when None, test the stored key


class OpenRouterKeyTestResponse(BaseModel):
    """Test-result response — the raw key value is NEVER included."""

    valid: bool
    credits_remaining: float | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/settings/openrouter-key", response_model=OpenRouterKeyStatusResponse)
async def get_openrouter_key_status() -> OpenRouterKeyStatusResponse:
    """Status of the stored OpenRouter API key — never returns the value itself."""
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        return OpenRouterKeyStatusResponse(configured=False)
    return OpenRouterKeyStatusResponse(
        configured=True,
        last4=key[-4:] if len(key) >= 4 else "****",
        updated_at=_get_key_updated_at("OPENROUTER_API_KEY"),
    )


@router.put("/settings/openrouter-key", response_model=OpenRouterKeyStatusResponse)
async def put_openrouter_key(req: OpenRouterKeyPutRequest) -> OpenRouterKeyStatusResponse:
    """Write-only endpoint: accepts the key, stores it, returns status-only.

    The stored value is written to both .env (persistence across restarts) and
    os.environ (takes effect immediately). The response body NEVER contains
    the key itself — only last4 + timestamp.
    """
    _update_env_file({"OPENROUTER_API_KEY": req.api_key})
    os.environ["OPENROUTER_API_KEY"] = req.api_key
    _set_key_updated_at("OPENROUTER_API_KEY")
    return OpenRouterKeyStatusResponse(
        configured=True,
        last4=req.api_key[-4:] if len(req.api_key) >= 4 else "****",
        updated_at=_get_key_updated_at("OPENROUTER_API_KEY"),
    )


@router.post("/settings/openrouter-key/test", response_model=OpenRouterKeyTestResponse)
async def test_openrouter_key(req: OpenRouterKeyTestRequest) -> OpenRouterKeyTestResponse:
    """Validate a key (provided OR stored) against /auth/key without storing.

    Returns {valid, credits_remaining, error}. Never echoes the key value.
    """
    api_key = req.api_key or os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return OpenRouterKeyTestResponse(valid=False, error="No key provided or stored")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            limit = data.get("limit_remaining")
            return OpenRouterKeyTestResponse(
                valid=True,
                credits_remaining=float(limit) if limit is not None else None,
            )
        if resp.status_code == 401:
            return OpenRouterKeyTestResponse(valid=False, error="Invalid API key (401)")
        return OpenRouterKeyTestResponse(
            valid=False, error=f"Unexpected status {resp.status_code}"
        )
    except (httpx.HTTPError, OSError) as exc:
        return OpenRouterKeyTestResponse(valid=False, error=f"Network error: {exc}")
