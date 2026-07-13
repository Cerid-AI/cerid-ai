# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cloud-connector status + OAuth surface (deferred Phase F.2 cleanup).

Unified REST surface over the sibling-MCP Pro connectors (Google
Workspace + ms365) and the Swift-helper-backed Apple connectors. Lets
the desktop wizard ask "which connectors are configured?" + "is this
one's OAuth complete?" without per-provider hacks.

Endpoints:

  GET    /connectors                       → list with status per
  GET    /connectors/{slug}                → one connector's detail
  POST   /connectors/{slug}/auth/start     → initiate OAuth flow
  GET    /connectors/{slug}/auth/status    → poll auth state
  POST   /connectors/{slug}/disconnect     → revoke / clear stored creds

Source of truth for "is X configured": the connector's DataSource
``is_configured()`` method. Source of truth for "is the MCP server
reachable": MCPClientPool.list_connectors() circuit state.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("ai-companion.connectors")

router = APIRouter(prefix="/connectors", tags=["connectors"])


# ── connector inventory ───────────────────────────────────────────────

# Each connector ships with this metadata. ``auth_kind`` describes how
# the operator completes the OAuth flow — different connectors have
# different shapes (Google's single-user OAuth callback vs Microsoft's
# device-code vs Apple's TCC-only no-OAuth path).
class ConnectorMeta(BaseModel):
    slug: str
    display_name: str
    feature_flag: str
    auth_kind: Literal["google_oauth", "msal_device_code", "tcc_only"]
    requires_env: list[str]
    requires_sibling: str | None  # e.g. "google_workspace" / "ms365" / None for Apple
    data_source_name: str | None  # registry key; None if not a DataSource (e.g. spotlight_donor)
    instruction_doc: str  # relative path to operator-facing docs
    # Operator-facing explainer (P0-C.4): what the connector reads, whether
    # it syncs continuously / imports once / reads on demand, and where the
    # resulting data lands. Rendered in ConnectorDetail + the connector rows.
    imports_desc: str  # what it reads/imports
    sync_semantics: str  # continuous-sync vs one-time vs on-demand
    lands_in: str  # where the data ends up


_CONNECTORS: dict[str, ConnectorMeta] = {
    "gmail": ConnectorMeta(
        slug="gmail",
        display_name="Gmail",
        feature_flag="gmail_connector",
        auth_kind="google_oauth",
        requires_env=["CERID_CONNECTORS_BEARER", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"],
        requires_sibling="google_workspace",
        data_source_name="gmail",
        instruction_doc="docs/PRO_GMAIL.md",
        imports_desc="Email messages and threads matching your queries.",
        sync_semantics="On-demand lookup while connected — searches your mailbox when a question needs it. No one-time import, no watch folder; your mailbox is never bulk-copied.",
        lands_in="Chat answers with citations; inbox-triage briefs when that automation is enabled. Nothing is written to the knowledge base.",
    ),
    "google_calendar": ConnectorMeta(
        slug="google_calendar",
        display_name="Google Calendar",
        feature_flag="google_calendar_sync",
        auth_kind="google_oauth",
        requires_env=["CERID_CONNECTORS_BEARER", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"],
        requires_sibling="google_workspace",
        data_source_name="google_calendar",
        instruction_doc="docs/PRO_GOOGLE_CALENDAR.md",
        imports_desc="Calendar events (titles, times, attendees).",
        sync_semantics="On-demand lookup while connected — events are fetched when a question or a meeting capture needs them. No one-time import; your calendar is never bulk-copied.",
        lands_in="Chat answers and meeting-capture context. Nothing is written to the knowledge base.",
    ),
    "outlook": ConnectorMeta(
        slug="outlook",
        display_name="Outlook Mail",
        feature_flag="outlook_connector",
        auth_kind="msal_device_code",
        requires_env=["CERID_CONNECTORS_BEARER"],
        requires_sibling="ms365",
        data_source_name="outlook",
        instruction_doc="docs/PRO_OUTLOOK.md",
        imports_desc="Email messages and threads matching your queries.",
        sync_semantics="On-demand lookup while connected — searches your mailbox when a question needs it. No one-time import, no watch folder; your mailbox is never bulk-copied.",
        lands_in="Chat answers with citations. Nothing is written to the knowledge base.",
    ),
    "outlook_calendar": ConnectorMeta(
        slug="outlook_calendar",
        display_name="Outlook Calendar",
        feature_flag="outlook_calendar_sync",
        auth_kind="msal_device_code",
        requires_env=["CERID_CONNECTORS_BEARER"],
        requires_sibling="ms365",
        data_source_name="outlook_calendar",
        instruction_doc="docs/PRO_OUTLOOK.md",
        imports_desc="Calendar events (titles, times, attendees).",
        sync_semantics="On-demand lookup while connected — events are fetched when a question or a meeting capture needs them. No one-time import; your calendar is never bulk-copied.",
        lands_in="Chat answers and meeting-capture context. Nothing is written to the knowledge base.",
    ),
    "apple_calendar": ConnectorMeta(
        slug="apple_calendar",
        display_name="Apple Calendar",
        feature_flag="apple_calendar_eventkit",
        auth_kind="tcc_only",
        requires_env=[],
        requires_sibling=None,
        data_source_name="apple_calendar",
        instruction_doc="docs/PRO_APPLE_CALENDAR.md",
        imports_desc="Calendar events read locally via the EventKit helper.",
        sync_semantics="On-demand local read while access is granted — events are read from this Mac when a question needs them. No import step; nothing leaves the machine.",
        lands_in="Chat answers and meeting-capture context. Nothing is written to the knowledge base.",
    ),
    "apple_photos": ConnectorMeta(
        slug="apple_photos",
        display_name="Apple Photos",
        feature_flag="apple_photos_reader",
        auth_kind="tcc_only",
        requires_env=[],
        requires_sibling=None,
        data_source_name="apple_photos",
        instruction_doc="docs/PRO_APPLE_PHOTOS.md",
        imports_desc="Photo metadata (dates, places, albums) via the PhotoKit helper — never image files.",
        sync_semantics="On-demand local read while access is granted — metadata is read from this Mac when a question needs it. No import step; photos are not copied.",
        lands_in="Chat answers. Nothing is written to the knowledge base.",
    ),
    "apple_reminders": ConnectorMeta(
        slug="apple_reminders",
        display_name="Apple Reminders",
        feature_flag="reminders_eventkit",
        auth_kind="tcc_only",
        requires_env=[],
        requires_sibling=None,
        data_source_name="apple_reminders",
        instruction_doc="docs/PRO_APPLE_REMINDERS.md",
        imports_desc="Reminders and their due dates read locally via the EventKit helper.",
        sync_semantics="On-demand local read while access is granted — reminders are read from this Mac when a question needs them. No import step; nothing leaves the machine.",
        lands_in="Chat answers. Nothing is written to the knowledge base.",
    ),
}


def oauth_connector_kinds() -> set[str]:
    """Source kinds reachable via the OAuth/system-permission flow at
    ``/connectors/*`` (rather than the SourceConnector wizard). Used by
    ``/sources/kinds`` to mark these as connectable-via-OAuth."""
    return set(_CONNECTORS.keys())


# ── response shapes ───────────────────────────────────────────────────

class ConnectorStatus(BaseModel):
    slug: str
    display_name: str
    feature_flag: str
    feature_enabled: bool
    env_complete: bool
    missing_env: list[str]
    data_source_registered: bool
    data_source_configured: bool
    sibling_reachable: bool | None  # None when no sibling needed
    sibling_circuit_open: bool | None
    auth_kind: str
    instruction_doc: str
    imports_desc: str
    sync_semantics: str
    lands_in: str


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorStatus]


class ConnectorOAuthStartResponse(BaseModel):
    auth_kind: str
    # Google: returns an open-URL the operator visits to complete the flow
    auth_url: str | None = None
    # Microsoft device-code: returns the code + verification URI
    device_code: str | None = None
    verification_uri: str | None = None
    expires_in: int | None = None
    # Apple TCC: returns the System Settings deep-link
    settings_url: str | None = None
    instructions: str


class OAuthStatusResponse(BaseModel):
    slug: str
    completed: bool
    detail: str


class DisconnectResponse(BaseModel):
    slug: str
    cleared: bool
    detail: str


# ── status assembly ───────────────────────────────────────────────────

def _build_status(meta: ConnectorMeta) -> ConnectorStatus:
    from config.features import is_feature_enabled

    missing_env = [v for v in meta.requires_env if not os.getenv(v)]

    data_source_registered = False
    data_source_configured = False
    if meta.data_source_name:
        try:
            from app.data_sources import registry
            ds = registry.get(meta.data_source_name)
            if ds is not None:
                data_source_registered = True
                data_source_configured = bool(ds.is_configured())
        except ImportError:
            pass

    sibling_reachable: bool | None = None
    sibling_circuit_open: bool | None = None
    if meta.requires_sibling:
        try:
            from core.mcp_clients.client_pool import get_pool
            pool_state = {c["name"]: c for c in get_pool().list_connectors()}
            sibling = pool_state.get(meta.requires_sibling)
            if sibling is not None:
                sibling_circuit_open = bool(sibling.get("circuit_open", False))
                sibling_reachable = not sibling_circuit_open
            else:
                sibling_reachable = False
                sibling_circuit_open = False
        except ImportError:
            sibling_reachable = False
            sibling_circuit_open = False

    return ConnectorStatus(
        slug=meta.slug,
        display_name=meta.display_name,
        feature_flag=meta.feature_flag,
        feature_enabled=bool(is_feature_enabled(meta.feature_flag)),
        env_complete=not missing_env,
        missing_env=missing_env,
        data_source_registered=data_source_registered,
        data_source_configured=data_source_configured,
        sibling_reachable=sibling_reachable,
        sibling_circuit_open=sibling_circuit_open,
        auth_kind=meta.auth_kind,
        instruction_doc=meta.instruction_doc,
        imports_desc=meta.imports_desc,
        sync_semantics=meta.sync_semantics,
        lands_in=meta.lands_in,
    )


# ── endpoints ─────────────────────────────────────────────────────────

@router.get("", response_model=ConnectorListResponse)
async def list_connectors() -> ConnectorListResponse:
    """List all known connectors with their current status."""
    return ConnectorListResponse(
        connectors=[_build_status(meta) for meta in _CONNECTORS.values()],
    )


@router.get("/{slug}", response_model=ConnectorStatus)
async def get_connector(slug: str) -> ConnectorStatus:
    meta = _CONNECTORS.get(slug)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown connector: {slug}")
    return _build_status(meta)


@router.post("/{slug}/auth/start", response_model=ConnectorOAuthStartResponse)
async def start_auth(slug: str) -> ConnectorOAuthStartResponse:
    meta = _CONNECTORS.get(slug)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown connector: {slug}")

    if meta.auth_kind == "google_oauth":
        port = os.getenv("CERID_PORT_GOOGLE_MCP", "8810")
        auth_url = f"http://127.0.0.1:{port}/oauth/start"
        return ConnectorOAuthStartResponse(
            auth_kind="google_oauth",
            auth_url=auth_url,
            instructions=(
                f"Open {auth_url} in your browser to complete Google OAuth. "
                f"The token will be persisted in the google-workspace-mcp container."
            ),
        )

    if meta.auth_kind == "msal_device_code":
        # Surface the device-code via the sibling's CLI surface (operator
        # runs `docker compose exec ms365-mcp ms365-mcp login`).
        return ConnectorOAuthStartResponse(
            auth_kind="msal_device_code",
            instructions=(
                "Run: docker compose -f stacks/connectors/docker-compose.yml "
                "exec ms365-mcp ms365-mcp login — it prints a code + URL. "
                "Visit microsoft.com/devicelogin, paste the code, complete login. "
                "Token cached to the bind-mounted volume."
            ),
        )

    # tcc_only — open System Settings → Privacy & Security
    settings_url = "x-apple.systempreferences:com.apple.preference.security?Privacy"
    return ConnectorOAuthStartResponse(
        auth_kind="tcc_only",
        settings_url=settings_url,
        instructions=(
            f"Open System Settings → Privacy & Security and grant access "
            f"to the data category for {meta.display_name}. Then relaunch "
            f"Cerid AI (TCC cache requires it). See {meta.instruction_doc}."
        ),
    )


@router.get("/{slug}/auth/status", response_model=OAuthStatusResponse)
async def get_auth_status(slug: str) -> OAuthStatusResponse:
    """Poll endpoint for the wizard. "Completed" maps to:
       - Google/Microsoft: data_source.is_configured() AND sibling reachable
       - Apple: data_source.is_configured() (helper present + Darwin)
    """
    meta = _CONNECTORS.get(slug)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown connector: {slug}")

    status = _build_status(meta)
    if meta.auth_kind in ("google_oauth", "msal_device_code"):
        completed = (
            status.feature_enabled
            and status.env_complete
            and status.data_source_configured
            and bool(status.sibling_reachable)
        )
        if completed:
            detail = "OAuth complete; sibling MCP reachable."
        elif not status.env_complete:
            detail = f"Missing env vars: {status.missing_env}"
        elif not status.sibling_reachable:
            detail = f"Sibling {meta.requires_sibling} not reachable. " \
                     "Bring up the connector stack via docker compose."
        else:
            detail = "OAuth incomplete or token expired."
    else:
        completed = status.feature_enabled and status.data_source_configured
        detail = "TCC granted." if completed else "TCC denied or helper missing."

    return OAuthStatusResponse(slug=slug, completed=completed, detail=detail)


@router.post("/{slug}/disconnect", response_model=DisconnectResponse)
async def disconnect(slug: str) -> DisconnectResponse:
    """Disconnect a connector. For sibling-MCP connectors this is a
    no-op on Cerid's side (operator must docker exec the sibling to
    clear tokens). For Apple connectors there's nothing to disconnect
    — the user revokes TCC in System Settings."""
    meta = _CONNECTORS.get(slug)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown connector: {slug}")

    if meta.auth_kind == "google_oauth":
        return DisconnectResponse(
            slug=slug,
            cleared=False,
            detail=(
                "Run: docker compose -f stacks/connectors/docker-compose.yml "
                "exec google-workspace-mcp rm -rf /root/.google_workspace_mcp/credentials"
                " — then restart the container."
            ),
        )
    if meta.auth_kind == "msal_device_code":
        return DisconnectResponse(
            slug=slug,
            cleared=False,
            detail=(
                "Run: docker compose -f stacks/connectors/docker-compose.yml "
                "exec ms365-mcp ms365-mcp logout"
            ),
        )
    return DisconnectResponse(
        slug=slug,
        cleared=False,
        detail=(
            "Revoke in System Settings → Privacy & Security, then relaunch Cerid."
        ),
    )
