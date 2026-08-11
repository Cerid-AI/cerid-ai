# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

import json
import logging
import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.connectors")

router = APIRouter(prefix="/connectors", tags=["connectors"])


# ── connector inventory ───────────────────────────────────────────────

# Each connector ships with this metadata. ``auth_kind`` describes how
# the operator completes the OAuth flow — different connectors have
# different shapes (Google's single-user OAuth callback vs Microsoft's
# device-code vs Apple's TCC-only no-OAuth path).
#
# Apple Notes / Mail / iMessage are deliberately NOT listed here. Sources →
# Connectors builds its rows by concatenating /connectors with the Electron
# bridge rows in src/web/.../source-rows.ts (APPLE_BRIDGE_KINDS), with no
# dedup, and those three already ingest through the bridge
# (packages/desktop/src/main/connectors/*.ts). Adding them would render each
# one twice in the desktop build — once working, once reporting a Swift helper
# the container cannot see. Calendar / Photos / Reminders are the inverse: REST
# rows here, deliberately excluded from APPLE_BRIDGE_KINDS. Keep that split;
# test_connectors_router.py pins it.
class ConnectorMeta(BaseModel):
    slug: str
    display_name: str
    feature_flag: str
    auth_kind: Literal["google_oauth", "msal_device_code", "tcc_only"]
    requires_env: list[str]
    requires_sibling: str | None  # e.g. "google_workspace" / "ms365" / None for Apple
    data_source_name: str | None  # registry key; None if the connector is not a DataSource
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
    # Which sibling MCP this connector needs, or None for the Apple/TCC ones.
    # Without it ``sibling_reachable: null`` is ambiguous — it means both "no
    # sibling required" and "required but never contacted", and the UI was
    # hiding the second case as though it were the first.
    requires_sibling: str | None
    sibling_reachable: bool | None  # None = no sibling, or not contacted yet
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
    # Stays None for ms365: the sibling's login reply carries no expiry of any
    # kind, and MSAL's default would be a number we invented rather than one it
    # told us. An absent field is honest; a fabricated one expires the code in
    # the UI at a time the sibling never agreed to.
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


_URL_RE = re.compile(r"https://\S+")


def _first_url(text: str) -> str | None:
    """First https URL in the sibling's free-text reply.

    ``start_google_auth`` returns prose with the consent link embedded rather
    than a structured field, so there is nothing better to key on.
    """
    match = _URL_RE.search(text)
    return match.group(0).rstrip(").,'\"") if match else None


# MSAL's device-code prose: "... enter the code VDBF68NR to authenticate."
# The capture is uppercase-only on purpose: made case-insensitive it matches the
# next ordinary word of a differently-worded sentence ("enter the code shown in
# your browser" → "shown"), handing the operator a word to type into Microsoft's
# form instead of a code, with no way to tell it was never one.
_DEVICE_CODE_RE = re.compile(r"[Cc]ode\s+([A-Z0-9]{6,})\b")

# Fallback for an ms365 sibling that is down or answers something we cannot
# read. Kept as the operator's escape hatch: losing it strands whoever most
# needs it — the one whose connector stack will not start.
_MS365_MANUAL_LOGIN = (
    "Run: docker compose -f docker-compose.yml -f stacks/connectors/docker-compose.yml --profile pro "
    "exec ms365-mcp node dist/index.js --login — it prints a code + URL. "
    "Open the URL it prints, enter the code, and complete the Microsoft "
    "sign-in. Token cached to the bind-mounted volume."
)


def _parse_device_code(text: str) -> tuple[str | None, str | None]:
    """``(device_code, verification_uri)`` from the ms365 sibling's login reply.

    The reply is prose inside a JSON envelope: ``structuredContent`` is None,
    ``isError`` is False, and the top-level key is ``"error":
    "device_code_required"`` even on the success path — so there is nothing to
    branch on and the code + URL have to be read out of the free-text
    ``message``. Same shape of problem ``_first_url`` already solves for Google.
    """
    message = text
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        message = payload["message"]

    match = _DEVICE_CODE_RE.search(message)
    return (match.group(1) if match else None), _first_url(message)


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
                if sibling_circuit_open:
                    sibling_reachable = False
                elif sibling.get("ever_succeeded"):
                    sibling_reachable = True
                else:
                    # Registered, breaker closed, never actually talked to.
                    # Reporting True here is what made a connector whose
                    # container was not running read as "connected" — the
                    # breaker needs three failures before it opens, and
                    # nothing had called it even once. None means "unknown",
                    # and that is the truth until a call succeeds.
                    sibling_reachable = None
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
        requires_sibling=meta.requires_sibling,
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

    # Gate the GRANT. `feature_enabled` was computed for /connectors and only
    # ever reported, so a community install could walk a real Google consent
    # screen and have a refresh token written into the sibling's volume. The
    # ingest path would then refuse to use it — value withheld, but the
    # credential handed over anyway, which is the wrong half to get right.
    #
    # Deliberately NOT applied to /disconnect or /auth/status: gating disconnect
    # would stop a user REVOKING access they should always be able to revoke,
    # and status is read-only. A gate that blocks the safety action is worse
    # than no gate.
    from config.features import is_feature_enabled

    if not is_feature_enabled(meta.feature_flag):
        raise HTTPException(
            status_code=403,
            detail=(
                f"{meta.display_name} requires Cerid Pro — not starting an "
                "authorization the server would refuse to use."
            ),
        )

    if meta.auth_kind == "google_oauth":
        # The sibling has no browsable start page — /oauth/start is a 404, and
        # this endpoint returned that URL from Phase F until 2026-08-09, the
        # first time anyone ran the container. The flow is an MCP tool call
        # (``start_google_auth``) that returns the consent URL; the server's
        # only HTTP route is the /oauth2callback the consent screen redirects
        # back to.
        email = os.getenv("USER_GOOGLE_EMAIL", "").strip()
        if not email:
            return ConnectorOAuthStartResponse(
                auth_kind="google_oauth",
                instructions=(
                    "Set USER_GOOGLE_EMAIL in .env to the Google account to "
                    "connect, recreate the connector stack, then retry — the "
                    "sibling needs an account to start the consent flow for."
                ),
            )
        try:
            from core.mcp_clients.client_pool import get_pool
            raw = await get_pool().call_tool(
                meta.requires_sibling or "google_workspace",
                "start_google_auth",
                {"service_name": "gmail", "user_google_email": email},
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the operator below
            log_swallowed_error("app.routers.connectors.start_google_auth", exc)
            return ConnectorOAuthStartResponse(
                auth_kind="google_oauth",
                instructions=(
                    f"Could not reach {meta.requires_sibling}: {exc}. Bring the "
                    "connector stack up (--profile pro) and retry."
                ),
            )
        # tool_text(), not str(): str() on a CallToolResult gives the pydantic
        # repr, in which a newline is the two literal characters \ and n. The
        # URL regex matches \S+, so it ran straight past the end of the URL and
        # handed the operator a consent link ending "prompt=select_account\nMarkdown".
        from core.mcp_clients.result_text import tool_text

        raw_text = tool_text(raw)
        auth_url = _first_url(raw_text)
        return ConnectorOAuthStartResponse(
            auth_kind="google_oauth",
            auth_url=auth_url,
            instructions=(
                f"Open the returned URL in a browser on this machine and consent "
                f"to the read-only scopes for {email}. The refresh token is "
                f"written to the google-workspace-mcp container's volume."
                if auth_url
                else f"The sibling did not return a consent URL: {raw_text[:300]}"
            ),
        )

    if meta.auth_kind == "msal_device_code":
        # `login` is a live MCP tool on the sibling — the connector stack passes
        # --enable-auth-tools, which is exactly what makes the device-code flow
        # drivable over HTTP. Until 2026-08-10 this branch returned a string
        # telling the operator to `docker compose exec` and read a code off a
        # container's stdout, which no desktop user can do; the declared
        # device_code / verification_uri fields were never assigned by anything.
        #
        # Route through the pool, never a bare MCPHTTPClient: the sibling 401s
        # the whole transport without an Authorization header, and the pool is
        # what attaches it.
        try:
            from core.mcp_clients.client_pool import get_pool
            raw = await get_pool().call_tool(
                meta.requires_sibling or "ms365", "login", {},
            )
        except Exception as exc:  # noqa: BLE001 — falls back to the manual path
            log_swallowed_error("app.routers.connectors.ms365_login", exc)
            return ConnectorOAuthStartResponse(
                auth_kind="msal_device_code",
                instructions=(
                    f"Could not reach {meta.requires_sibling}: {exc}. "
                    f"{_MS365_MANUAL_LOGIN}"
                ),
            )

        # MCP reports tool failure as a RESULT carrying isError, not as an
        # exception, so the except above never fires for e.g. an unknown tool
        # name or a rejected bearer. Checking the flag is the only way to tell
        # those apart from a reply we simply could not parse.
        from core.mcp_clients.result_text import is_error_result, tool_text

        raw_text = tool_text(raw)
        device_code, verification_uri = (
            (None, None) if is_error_result(raw) else _parse_device_code(raw_text)
        )
        if not device_code or not verification_uri:
            return ConnectorOAuthStartResponse(
                auth_kind="msal_device_code",
                instructions=(
                    "The ms365 sibling did not return a device code "
                    f"({raw_text[:300] or 'empty reply'}). {_MS365_MANUAL_LOGIN}"
                ),
            )
        return ConnectorOAuthStartResponse(
            auth_kind="msal_device_code",
            device_code=device_code,
            verification_uri=verification_uri,
            instructions=(
                f"Open {verification_uri} in a browser and enter the code "
                f"{device_code}, then sign in to the Microsoft account to "
                "connect. The token is cached to the ms365 container's volume; "
                "this page reports connected once the sibling answers."
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
        elif status.sibling_reachable is None:
            # Distinct from "not reachable": nothing has called it yet, so we
            # have no evidence either way. Saying "not reachable" here sent
            # operators to debug a container that was fine.
            detail = (
                f"Sibling {meta.requires_sibling} has not been contacted yet — "
                "run a query through this connector, or bring the stack up if "
                "it is not running."
            )
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
                "Run: docker compose -f docker-compose.yml -f stacks/connectors/docker-compose.yml --profile pro "
                "exec google-workspace-mcp rm -rf /home/app/.google_workspace_mcp"
                " — then restart the container. (This said /root until "
                "2026-08-10; the server runs as uid 1000 with HOME=/home/app, "
                "so the old path cleared nothing and reported no error.)"
            ),
        )
    if meta.auth_kind == "msal_device_code":
        return DisconnectResponse(
            slug=slug,
            cleared=False,
            detail=(
                "Run: docker compose -f docker-compose.yml -f stacks/connectors/docker-compose.yml --profile pro "
                "exec ms365-mcp node dist/index.js --logout"
            ),
        )
    return DisconnectResponse(
        slug=slug,
        cleared=False,
        detail=(
            "Revoke in System Settings → Privacy & Security, then relaunch Cerid."
        ),
    )
