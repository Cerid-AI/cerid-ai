# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Webhook subscription CRUD — manage HTTP callback registrations (Phase 3 — extensibility).

Subscriptions are stored in Redis:
- ``cerid:webhooks:sub:{id}`` — JSON-serialised subscription record
- ``cerid:webhooks:deliveries:{id}`` — capped list (last 100) of delivery records

Delivery payloads are HMAC-signed (SHA-256) using the subscription's secret so
receivers can verify authenticity.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from deps import get_redis
from utils.time import utcnow_iso

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger("ai-companion.webhooks")

_KEY_PREFIX = "cerid:webhooks:sub:"
_DELIVERY_PREFIX = "cerid:webhooks:deliveries:"
_MAX_DELIVERIES = 100


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WebhookSubscriptionCreate(BaseModel):
    """Request body for creating a webhook subscription."""

    url: HttpUrl = Field(..., description="HTTPS endpoint that will receive POST callbacks")
    events: list[str] = Field(
        default_factory=list,
        description="Event types to subscribe to (empty = all events)",
    )
    secret: str = Field(
        default="",
        max_length=256,
        description="Shared secret for HMAC-SHA256 payload signing",
    )
    active: bool = Field(default=True, description="Whether deliveries are enabled")
    description: str = Field(default="", max_length=512)


class WebhookSubscriptionUpdate(BaseModel):
    """Partial update for a webhook subscription (PATCH semantics)."""

    url: HttpUrl | None = None
    events: list[str] | None = None
    secret: str | None = Field(default=None, max_length=256)
    active: bool | None = None
    description: str | None = Field(default=None, max_length=512)


class WebhookSubscription(BaseModel):
    """Full representation of a stored webhook subscription."""

    id: str
    url: str
    events: list[str] = Field(default_factory=list)
    secret: str = ""
    active: bool = True
    description: str = ""
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sub_key(sub_id: str) -> str:
    return f"{_KEY_PREFIX}{sub_id}"


def _delivery_key(sub_id: str) -> str:
    return f"{_DELIVERY_PREFIX}{sub_id}"


def _load_sub(r, sub_id: str) -> dict[str, Any] | None:
    raw = r.get(_sub_key(sub_id))
    if not raw:
        return None
    return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))


def _save_sub(r, sub_id: str, data: dict[str, Any]) -> None:
    r.set(_sub_key(sub_id), json.dumps(data))


def sign_payload(payload: str, secret: str) -> str:
    """Compute HMAC-SHA256 signature of *payload* using *secret*.

    Returns an empty string when secret is empty (no signing configured).
    """
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/webhooks")
async def list_subscriptions():
    """List all webhook subscriptions."""
    r = get_redis()
    keys = r.keys(f"{_KEY_PREFIX}*")
    subs: list[dict[str, Any]] = []
    for key in keys:
        raw = r.get(key)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            # Redact secret in listing
            data["secret"] = "***" if data.get("secret") else ""
            subs.append(data)
    return {"subscriptions": subs, "total": len(subs)}


@router.post("/webhooks", status_code=201)
async def create_subscription(body: WebhookSubscriptionCreate):
    """Create a new webhook subscription."""
    r = get_redis()
    sub_id = str(uuid.uuid4())
    now = utcnow_iso()
    data = {
        "id": sub_id,
        "url": str(body.url),
        "events": body.events,
        "secret": body.secret,
        "active": body.active,
        "description": body.description,
        "created_at": now,
        "updated_at": now,
    }
    _save_sub(r, sub_id, data)
    logger.info("Created webhook subscription %s -> %s", sub_id[:8], body.url)
    # Redact secret in response
    resp = {**data, "secret": "***" if data["secret"] else ""}
    return resp


@router.get("/webhooks/{sub_id}")
async def get_subscription(sub_id: str):
    """Retrieve a single webhook subscription by ID."""
    r = get_redis()
    data = _load_sub(r, sub_id)
    if not data:
        raise HTTPException(status_code=404, detail="Subscription not found")
    data["secret"] = "***" if data.get("secret") else ""
    return data


@router.patch("/webhooks/{sub_id}")
async def update_subscription(sub_id: str, body: WebhookSubscriptionUpdate):
    """Partially update a webhook subscription."""
    r = get_redis()
    data = _load_sub(r, sub_id)
    if not data:
        raise HTTPException(status_code=404, detail="Subscription not found")

    updates = body.model_dump(exclude_unset=True)
    if "url" in updates and updates["url"] is not None:
        updates["url"] = str(updates["url"])
    data.update(updates)
    data["updated_at"] = utcnow_iso()
    _save_sub(r, sub_id, data)
    logger.info("Updated webhook subscription %s", sub_id[:8])
    data["secret"] = "***" if data.get("secret") else ""
    return data


@router.delete("/webhooks/{sub_id}")
async def delete_subscription(sub_id: str):
    """Delete a webhook subscription and its delivery history."""
    r = get_redis()
    if not r.exists(_sub_key(sub_id)):
        raise HTTPException(status_code=404, detail="Subscription not found")
    r.delete(_sub_key(sub_id))
    r.delete(_delivery_key(sub_id))
    logger.info("Deleted webhook subscription %s", sub_id[:8])
    return {"deleted": True, "id": sub_id}


@router.get("/webhooks/{sub_id}/deliveries")
async def list_deliveries(
    sub_id: str,
    limit: int = Query(20, ge=1, le=100),
):
    """List recent delivery attempts for a subscription (newest first)."""
    r = get_redis()
    if not r.exists(_sub_key(sub_id)):
        raise HTTPException(status_code=404, detail="Subscription not found")
    raw_items = r.lrange(_delivery_key(sub_id), 0, limit - 1)
    deliveries = []
    for item in raw_items:
        try:
            deliveries.append(json.loads(item if isinstance(item, str) else item.decode("utf-8")))
        except (json.JSONDecodeError, TypeError):
            continue
    return {"deliveries": deliveries, "total": len(deliveries)}
