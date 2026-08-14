# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Data source management — list, enable/disable preloaded and custom sources."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.data_sources import (
    DataSourceListResponse,
    DataSourceToggleResponse,
)
from utils.error_handler import handle_errors


# --- Response models (generated: single-return dict-literal routes) ---
class DeleteEmailSourceResponse(BaseModel):
    status: str


class ConfigureEmailResponse(BaseModel):
    status: str
    host: Any
    user: Any


router = APIRouter(tags=["data-sources"])
logger = logging.getLogger("ai-companion.data_sources")


@router.get("/data-sources", response_model=DataSourceListResponse)
async def list_data_sources():
    """List all registered data sources with their status.

    Enabled flags are hydrated from Redis on first access so persisted
    toggles survive process restarts (parity with /external-apis).
    """
    from app.data_sources import hydrate_enabled_state, registry
    hydrate_enabled_state()
    sources = registry.list_sources()
    return {"sources": sources, "total": len(sources)}


@router.post("/data-sources/{name}/enable", response_model=DataSourceToggleResponse)
async def enable_source(name: str):
    """Enable a registered data source by name.

    The flip is applied in-memory and written through to Redis. When Redis
    is unavailable the in-memory flip still succeeds (pre-persistence
    behaviour), so the response shape and status codes are unchanged.
    """
    from app.data_sources import hydrate_enabled_state, persist_enabled_state, registry
    hydrate_enabled_state()
    source = registry.get(name)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source '{name}' not found")
    source.enabled = True
    persist_enabled_state(name, True)
    return {"status": "enabled", "name": name}


@router.post("/data-sources/{name}/disable", response_model=DataSourceToggleResponse)
async def disable_source(name: str):
    """Disable a registered data source by name.

    Same persistence semantics as :func:`enable_source`.
    """
    from app.data_sources import hydrate_enabled_state, persist_enabled_state, registry
    hydrate_enabled_state()
    source = registry.get(name)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source '{name}' not found")
    source.enabled = False
    persist_enabled_state(name, False)
    return {"status": "disabled", "name": name}


# ── Email IMAP poller endpoints ────────────────────────────────────────────


class EmailConfigRequest(BaseModel):
    """IMAP connection configuration.

    Note: ``poll_interval`` is currently IGNORED. Email poll cadence is driven
    solely by the global ``SCHEDULE_EMAIL_POLL`` cron in the scheduler, not by
    any per-source value. The field is retained for API/forward-compatibility
    only — setting it has no effect on how often the mailbox is polled.
    """

    host: str
    port: int = 993
    user: str
    password: str
    folder: str = "INBOX"
    # IGNORED — cadence is the global SCHEDULE_EMAIL_POLL cron, not this value.
    # Retained for forward-compat / API stability only.
    poll_interval: int = 15  # minutes


@router.post("/data-sources/email/configure", response_model=ConfigureEmailResponse)
@handle_errors(breaker_name="email-imap")
async def configure_email(config: EmailConfigRequest):
    """Configure IMAP connection — validates connectivity before saving."""
    import imaplib

    from app.data_sources.email_imap import save_email_config

    try:
        await save_email_config(config.model_dump())
    except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
        # Unreachable host / bad port / wrong credentials / missing folder is
        # user input, not a server fault — return 422 instead of 500.
        # @handle_errors re-raises HTTPException without recording a breaker
        # failure, so a fat-fingered config doesn't trip the email-imap breaker.
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not validate the IMAP connection to {config.host}:{config.port} — "
                "check the host, port, credentials, and folder."
            ),
        ) from exc
    return {"status": "configured", "host": config.host, "user": config.user}


@router.get("/data-sources/email/status")  # response-model-allowed: dynamic response (shape varies)
@handle_errors(fallback={"last_poll": None, "messages_ingested": 0, "errors": []})
async def email_status():
    """Return current email polling status — last poll time, message count, errors."""
    from app.data_sources.email_imap import get_email_status

    return await get_email_status()


@router.delete("/data-sources/email", response_model=DeleteEmailSourceResponse)
@handle_errors()
async def delete_email_source():
    """Remove IMAP configuration and stop polling."""
    from app.data_sources.email_imap import delete_email_config

    await delete_email_config()
    return {"status": "deleted"}


@router.post("/data-sources/email/poll-now")  # response-model-allowed: dynamic response (shape varies)
@handle_errors(breaker_name="email-imap")
async def poll_email_now():
    """Trigger an immediate email poll."""
    from app.data_sources.email_imap import poll_email

    result = await poll_email()
    return result
