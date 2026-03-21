# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Event-driven webhook notifications.

Fires HTTP POST to configured endpoints when events occur.
Events: ingestion.complete, health.warning, digest.ready, rectify.findings
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

import config
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.webhooks")


async def fire_event(
    event_type: str,
    payload: dict[str, Any],
) -> int:
    """
    Send a webhook notification for the given event.

    Args:
        event_type: Event name (e.g. "ingestion.complete")
        payload: Event data to include in the POST body

    Returns:
        Number of webhooks successfully delivered.
    """
    hooks = config.WEBHOOK_ENDPOINTS
    if not hooks:
        return 0

    body = {
        "event": event_type,
        "timestamp": utcnow_iso(),
        "data": payload,
    }

    delivered = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for hook in hooks:
            url = hook.get("url", "")
            events = hook.get("events")
            if not url:
                continue
            # Filter by event type if events list is specified
            if events and event_type not in events:
                continue
            try:
                resp = await client.post(
                    url,
                    json=body,
                    headers={"Content-Type": "application/json", "User-Agent": "cerid-ai/1.0"},
                )
                if resp.status_code < 400:
                    delivered += 1
                    logger.debug(f"Webhook delivered: {event_type} -> {url} ({resp.status_code})")
                else:
                    logger.warning(f"Webhook failed: {event_type} -> {url} ({resp.status_code})")
            except Exception as e:
                logger.warning(f"Webhook error: {event_type} -> {url}: {e}")

    return delivered


async def notify_ingestion_complete(artifact_id: str, domain: str, filename: str, chunks: int) -> None:
    """Fire ingestion.complete event."""
    await fire_event("ingestion.complete", {
        "artifact_id": artifact_id,
        "domain": domain,
        "filename": filename,
        "chunks": chunks,
    })


async def notify_health_warning(status: str, detail: str = "") -> None:
    """Fire health.warning event."""
    await fire_event("health.warning", {"status": status, "detail": detail})


async def notify_rectify_findings(findings: int, detail: dict[str, Any] | None = None) -> None:
    """Fire rectify.findings event."""
    await fire_event("rectify.findings", {"total_findings": findings, **(detail or {})})
