# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""
Event-driven webhook notifications.

Fires HTTP POST to configured endpoints when events occur.
Events: ingestion.complete, health.warning, digest.ready, rectify.findings
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

import httpx

import config
from app.reliability.url_safety import guard_or_log
from core.utils.time import utcnow_iso
from deps import get_redis
from errors import CeridError

logger = logging.getLogger("ai-companion.webhooks")

#: Redis key prefix for subscriptions registered via the ``/webhooks`` CRUD
#: API (``app/routers/webhook_subscriptions.py``). fire_event() delivers to
#: these in addition to the env-var-derived WEBHOOK_ENDPOINTS list below —
#: without this, the CRUD API registers callbacks nothing ever calls.
_SUBSCRIPTION_KEY_PREFIX = "cerid:webhooks:sub:"

#: Redis key prefix for the per-subscription delivery history that
#: ``GET /webhooks/{id}/deliveries`` (app/routers/webhook_subscriptions.py)
#: reads from. Mirrors that router's own ``_DELIVERY_PREFIX`` /
#: ``_MAX_DELIVERIES`` — kept as a local constant rather than a cross-import
#: to match the ``_SUBSCRIPTION_KEY_PREFIX`` precedent above.
_DELIVERY_KEY_PREFIX = "cerid:webhooks:deliveries:"
_MAX_DELIVERIES = 100


def _registered_subscriptions() -> list[dict[str, Any]]:
    """Load active webhook subscriptions from the Redis-backed CRUD store."""
    hooks: list[dict[str, Any]] = []
    try:
        # redis-py connects lazily at command time, so the keys()/get() calls
        # — not get_redis() — are where an unreachable store actually raises.
        redis_client = get_redis()
        for key in redis_client.keys(f"{_SUBSCRIPTION_KEY_PREFIX}*"):
            raw = redis_client.get(key)
            if not raw:
                continue
            try:
                data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
                logger.warning("Malformed webhook subscription at %s: %s", key, exc)
                continue
            if data.get("active", True):
                hooks.append(data)
    except Exception as exc:  # noqa: BLE001 — no Redis means no subscriptions, not a crash
        logger.warning("Webhook subscription store unavailable: %s", exc)
        return []
    return hooks


def _record_delivery(
    sub_id: str,
    event_type: str,
    payload: dict[str, Any],
    status_code: int,
    error: str = "",
) -> None:
    """Append a delivery record for a CRUD-registered subscription.

    Read by ``GET /webhooks/{id}/deliveries``. Newest-first (LPUSH), capped
    at ``_MAX_DELIVERIES`` so the list can't grow unbounded. Only called for
    hooks that carry a subscription ``id`` — the env-var-derived
    WEBHOOK_ENDPOINTS list has no subscription to key history against.
    """
    try:
        redis_client = get_redis()
    except Exception as exc:  # noqa: BLE001 — history recording must not break delivery
        logger.warning("Webhook delivery-history store unavailable: %s", exc)
        return

    record: dict[str, Any] = {
        "event": event_type,
        "status_code": status_code,
        "delivered_at": utcnow_iso(),
        "payload": payload,
    }
    if error:
        record["error"] = error
    key = f"{_DELIVERY_KEY_PREFIX}{sub_id}"
    try:
        redis_client.lpush(key, json.dumps(record))
        redis_client.ltrim(key, 0, _MAX_DELIVERIES - 1)
    except Exception as exc:  # noqa: BLE001 — history recording must not break delivery
        logger.warning("Failed to record webhook delivery history for %s: %s", sub_id, exc)


def _validate_webhook_url(url: str) -> None:
    """Validate webhook URL scheme and hostname (non-empty).

    DNS-based SSRF prevention is handled separately by guard_or_log, which
    covers loopback, private, link-local, reserved, and multicast addresses
    and rejects DNS-resolution failures.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError(f"Webhook URL must use http(s): {url}")
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError(f"Webhook URL has no hostname: {url}")


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
    hooks = [*config.WEBHOOK_ENDPOINTS, *_registered_subscriptions()]
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
            # Scheme / hostname validation (non-SSRF)
            try:
                _validate_webhook_url(url)
            except ValueError as exc:
                logger.warning("Webhook URL validation failed: %s", exc)
                continue
            # SSRF prevention: DNS-resolved guard covers private, loopback,
            # link-local, reserved, multicast, and DNS-rebinding tricks.
            if not guard_or_log(url, source_name="webhooks"):
                continue
            headers = {"Content-Type": "application/json", "User-Agent": "cerid-ai/1.0"}
            secret = hook.get("secret", "")
            if secret:
                # CRUD-registered subscriptions carry a per-subscription HMAC
                # secret so receivers can verify authenticity.
                signature = hmac.new(
                    secret.encode("utf-8"),
                    json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Cerid-Signature"] = f"sha256={signature}"
            sub_id = hook.get("id", "")
            try:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code < HTTPStatus.BAD_REQUEST:
                    delivered += 1
                    logger.debug(f"Webhook delivered: {event_type} -> {url} ({resp.status_code})")
                else:
                    logger.warning(f"Webhook failed: {event_type} -> {url} ({resp.status_code})")
                if sub_id:
                    _record_delivery(sub_id, event_type, payload, resp.status_code)
            except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                logger.warning(f"Webhook error: {event_type} -> {url}: {e}")
                if sub_id:
                    _record_delivery(sub_id, event_type, payload, 0, error=str(e))

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
