# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/webhooks`` endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "WebhookSubscriptionResponse",
    "WebhookSubscriptionListResponse",
    "WebhookDeleteResponse",
    "WebhookDeliveryItem",
    "WebhookDeliveryListResponse",
]


class _WebhooksBase(BaseModel):
    """Base for all webhook response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class WebhookSubscriptionResponse(_WebhooksBase):
    """A single webhook subscription (returned from create, get, update)."""

    id: str = Field(description="Subscription UUID")
    url: str = Field(description="Callback URL")
    events: list[str] = Field(default_factory=list, description="Subscribed event types")
    secret: str = Field(default="", description="Redacted shared secret")
    active: bool = Field(default=True, description="Whether deliveries are enabled")
    description: str = Field(default="", description="User-provided description")
    created_at: str = Field(default="", description="ISO 8601 creation timestamp")
    updated_at: str = Field(default="", description="ISO 8601 last-update timestamp")


class WebhookSubscriptionListResponse(_WebhooksBase):
    """Response from ``GET /webhooks``."""

    subscriptions: list[WebhookSubscriptionResponse] = Field(
        default_factory=list, description="All webhook subscriptions"
    )
    total: int = Field(default=0, ge=0, description="Total subscription count")


class WebhookDeleteResponse(_WebhooksBase):
    """Response from ``DELETE /webhooks/{id}``."""

    deleted: bool = Field(default=True, description="Whether the subscription was deleted")
    id: str = Field(description="ID of the deleted subscription")


class WebhookDeliveryItem(_WebhooksBase):
    """A single delivery attempt record."""

    event: str = Field(default="", description="Event type")
    status_code: int = Field(default=0, description="HTTP response status code")
    delivered_at: str = Field(default="", description="ISO 8601 delivery timestamp")
    payload: dict[str, Any] = Field(default_factory=dict, description="Delivered payload")


class WebhookDeliveryListResponse(_WebhooksBase):
    """Response from ``GET /webhooks/{id}/deliveries``."""

    deliveries: list[dict[str, Any]] = Field(default_factory=list, description="Recent delivery records")
    total: int = Field(default=0, ge=0, description="Number of deliveries returned")
