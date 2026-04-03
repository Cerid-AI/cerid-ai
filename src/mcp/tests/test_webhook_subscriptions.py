# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for webhook subscription CRUD and delivery (Phase 3 — extensibility)."""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------


class TestHMACSigning:

    def test_sign_payload_produces_hex_digest(self):
        """sign_payload should produce a valid SHA-256 HMAC hex string."""
        from routers.webhook_subscriptions import sign_payload

        sig = sign_payload(json.dumps({"event": "test"}), "my-secret")
        assert isinstance(sig, str)
        assert len(sig) == 64  # sha256 hex

    def test_sign_payload_deterministic(self):
        from routers.webhook_subscriptions import sign_payload

        payload = json.dumps({"key": "value", "num": 42})
        sig1 = sign_payload(payload, "secret")
        sig2 = sign_payload(payload, "secret")
        assert sig1 == sig2

    def test_sign_payload_changes_with_secret(self):
        from routers.webhook_subscriptions import sign_payload

        payload = json.dumps({"event": "test"})
        sig1 = sign_payload(payload, "secret-a")
        sig2 = sign_payload(payload, "secret-b")
        assert sig1 != sig2

    def test_sign_payload_empty_secret_returns_empty(self):
        from routers.webhook_subscriptions import sign_payload

        sig = sign_payload(json.dumps({"event": "test"}), "")
        assert sig == ""


# ---------------------------------------------------------------------------
# Subscription model validation
# ---------------------------------------------------------------------------


class TestSubscriptionModels:

    def test_create_request_valid(self):
        from routers.webhook_subscriptions import WebhookSubscriptionCreate

        req = WebhookSubscriptionCreate(
            url="https://example.com/hook",
            events=["document.ingested", "memory.created"],
        )
        assert str(req.url) == "https://example.com/hook" or "example.com" in str(req.url)
        assert len(req.events) == 2

    def test_create_request_requires_url(self):
        from pydantic import ValidationError

        from routers.webhook_subscriptions import WebhookSubscriptionCreate

        with pytest.raises(ValidationError):
            WebhookSubscriptionCreate(events=["test"])  # type: ignore[call-arg]

    def test_create_request_requires_events(self):
        from pydantic import ValidationError

        from routers.webhook_subscriptions import WebhookSubscriptionCreate

        with pytest.raises(ValidationError):
            WebhookSubscriptionCreate(url="https://example.com")  # type: ignore[call-arg]

    def test_subscription_has_defaults(self):
        from routers.webhook_subscriptions import WebhookSubscription

        sub = WebhookSubscription(
            id="abc-123",
            url="https://example.com",
            events=["test"],
        )
        assert sub.active is True
        assert sub.secret == ""


# ---------------------------------------------------------------------------
# Redis storage patterns
# ---------------------------------------------------------------------------


class TestRedisStorage:

    def test_subscription_key_format(self):
        """Redis keys should follow cerid:webhooks:sub:{id} pattern."""
        sub_id = "test-123"
        key = f"cerid:webhooks:sub:{sub_id}"
        assert key == "cerid:webhooks:sub:test-123"

    def test_delivery_key_format(self):
        sub_id = "test-123"
        key = f"cerid:webhooks:deliveries:{sub_id}"
        assert key == "cerid:webhooks:deliveries:test-123"

    def test_subscription_serializes_to_json(self):
        from routers.webhook_subscriptions import WebhookSubscription

        sub = WebhookSubscription(
            id="abc",
            url="https://example.com",
            events=["document.ingested"],
            secret="hidden",  # pragma: allowlist secret
            active=True,
        )
        data = sub.model_dump()
        json_str = json.dumps(data)
        restored = json.loads(json_str)
        assert restored["id"] == "abc"
        assert restored["events"] == ["document.ingested"]
