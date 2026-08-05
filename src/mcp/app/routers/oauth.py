# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""OAuth router — entry + callback for Pro cloud connectors.

* ``POST /oauth/google/start`` → returns auth URL + state token
* ``GET  /oauth/google/callback`` → validates state, returns pending-
  token-exchange marker
* ``POST /oauth/microsoft/start`` → mirror for Outlook + Microsoft Calendar
* ``GET  /oauth/microsoft/callback``

State tokens are stored in Redis with a 10-minute TTL and single-use
(callback DELETEs on consume). Token exchange against the upstream
endpoints + credential handoff to the sibling MCP servers
(google_workspace, ms365) is configuration-driven and lives outside
this router.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import config
from app.deps import get_redis
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.routers.oauth")

router = APIRouter(prefix="/oauth", tags=["oauth"])

# Redis keys for the state-token store. State tokens are short-lived
# (10 min) and bind the auth-start request to its callback so the
# callback can't be replayed from a stale or attacker-supplied URL.
_STATE_TTL_S = 600
_STATE_PREFIX = "cerid:oauth:state:"

# Google scopes for the Gmail + Calendar bundle.
_GOOGLE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Microsoft scopes for the Outlook + Calendar bundle (MSAL / Graph).
_MICROSOFT_SCOPES = [
    "openid",
    "email",
    "offline_access",
    "Mail.Read",
    "Calendars.Read",
]


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str
    expires_at: int  # unix timestamp


class OAuthCallbackResponse(BaseModel):
    status: str
    source_id: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint_state(provider: str, redirect_uri: str) -> tuple[str, int]:
    """Generate a state token, store it in Redis with the provider +
    redirect_uri context, and return ``(state, expires_at)``.
    """
    state = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _STATE_TTL_S
    payload = {
        "provider": provider,
        "redirect_uri": redirect_uri,
        "expires_at": expires_at,
    }
    try:
        get_redis().set(
            f"{_STATE_PREFIX}{state}",
            __import__("json").dumps(payload),
            ex=_STATE_TTL_S,
        )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("oauth.mint_state", exc)
        raise HTTPException(status_code=503, detail="Redis unavailable for OAuth state") from exc
    return state, expires_at


def _consume_state(state: str) -> dict[str, Any]:
    """Validate + delete the state token. Raises on miss / expired."""
    import json as _json

    try:
        raw = get_redis().get(f"{_STATE_PREFIX}{state}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc

    if raw is None:
        raise HTTPException(status_code=400, detail="Unknown or expired state token")

    try:
        get_redis().delete(f"{_STATE_PREFIX}{state}")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("oauth.consume_state.delete", exc)

    try:
        return _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Corrupt state payload") from exc


# ---------------------------------------------------------------------------
# Google — Gmail + Calendar
# ---------------------------------------------------------------------------


@router.post("/google/start", response_model=OAuthStartResponse)
async def google_oauth_start(request: Request):
    """Begin the Google OAuth flow. Returns the URL the FE should
    open in a popup; the callback ``/oauth/google/callback`` lands
    back here with the code.
    """
    client_id = getattr(config, "GOOGLE_OAUTH_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(
            status_code=501,
            detail="GOOGLE_OAUTH_CLIENT_ID not configured",
        )

    redirect_uri = f"{str(request.base_url).rstrip('/')}/oauth/google/callback"
    state, expires_at = _mint_state("google", redirect_uri)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return OAuthStartResponse(auth_url=auth_url, state=state, expires_at=expires_at)


@router.get("/google/callback", response_model=OAuthCallbackResponse)
async def google_oauth_callback(code: str, state: str):
    """Validate the state token and acknowledge the OAuth code.

    The exchange against ``https://oauth2.googleapis.com/token``
    plus the sibling-MCP credential handoff is configuration-driven
    (CERID_CONNECTORS_BEARER + the google_workspace MCP). This
    endpoint guards the state token and returns the pending marker
    so the FE can transition past the popup.
    """
    _consume_state(state)
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code'")

    return OAuthCallbackResponse(
        status="pending_token_exchange",
        note="Code received and state validated. Configure the google_workspace sibling MCP to complete the exchange.",
    )


# ---------------------------------------------------------------------------
# Microsoft — Outlook + Calendar
# ---------------------------------------------------------------------------


@router.post("/microsoft/start", response_model=OAuthStartResponse)
async def microsoft_oauth_start(request: Request):
    client_id = getattr(config, "MICROSOFT_OAUTH_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(
            status_code=501,
            detail="MICROSOFT_OAUTH_CLIENT_ID not configured",
        )

    redirect_uri = f"{str(request.base_url).rstrip('/')}/oauth/microsoft/callback"
    state, expires_at = _mint_state("microsoft", redirect_uri)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_MICROSOFT_SCOPES),
        "response_mode": "query",
        "state": state,
    }
    tenant = getattr(config, "MICROSOFT_OAUTH_TENANT", "common")
    auth_url = (
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?"
        + urlencode(params)
    )
    return OAuthStartResponse(auth_url=auth_url, state=state, expires_at=expires_at)


@router.get("/microsoft/callback", response_model=OAuthCallbackResponse)
async def microsoft_oauth_callback(code: str, state: str):
    """Mirror of the Google callback for the Microsoft / Outlook bundle."""
    _consume_state(state)
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code'")
    return OAuthCallbackResponse(
        status="pending_token_exchange",
        note="Code received and state validated. Configure the ms365 sibling MCP to complete the exchange.",
    )
