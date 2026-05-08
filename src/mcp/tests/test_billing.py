# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Stripe billing router (Workstream C — Pro tier checkout).

Covers the end-to-end checkout + webhook + manual-key flow without
hitting live Stripe. The ``stripe`` module is replaced in
``sys.modules`` with a ``MagicMock`` so the lazy import inside
``routers.billing._get_stripe`` returns the mock; Redis is replaced
via ``patch("routers.billing.get_redis")``.

Why mocked, not live: Stripe's test mode requires a real API key on
the runner, and webhook signature verification needs a real secret.
The mocking strategy keeps tests deterministic and < 1 s while still
exercising every route + handler branch.
"""

from __future__ import annotations

import json
import sys
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def stripe_module_mock(monkeypatch):
    """Inject a stripe MagicMock into sys.modules so the lazy import
    inside billing._get_stripe() picks it up. Yields the mock so tests
    can configure return_values per-case."""
    mock = MagicMock()
    # Default behaviours — tests can override
    mock.checkout.Session.create.return_value = MagicMock(
        url="https://checkout.stripe.com/test_session_xyz",
        id="cs_test_xyz",
    )
    mock.Webhook.construct_event = MagicMock()
    monkeypatch.setitem(sys.modules, "stripe", mock)
    yield mock


@pytest.fixture
def fake_redis():
    """In-memory Redis substitute that supports the small subset
    billing.py uses: get/set/delete/sadd/scard."""
    state: dict = {}
    sets: dict = {}

    fake = MagicMock()

    def _set(key, value):
        state[key] = value
        return True

    def _get(key):
        return state.get(key)

    def _delete(*keys):
        for k in keys:
            state.pop(k, None)
        return len(keys)

    def _sadd(key, *members):
        s = sets.setdefault(key, set())
        before = len(s)
        s.update(members)
        return len(s) - before

    def _scard(key):
        return len(sets.get(key, set()))

    fake.set.side_effect = _set
    fake.get.side_effect = _get
    fake.delete.side_effect = _delete
    fake.sadd.side_effect = _sadd
    fake.scard.side_effect = _scard
    fake._state = state
    fake._sets = sets
    return fake


@pytest.fixture
def client(monkeypatch, fake_redis):
    """FastAPI TestClient wrapping just the billing router so tests
    don't pay for the full ``app.main`` startup cost."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_test_pro")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_dummy")
    # Re-read module-level constants that snapshot env at import time
    import routers.billing as billing
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(billing, "STRIPE_PRICE_ID_PRO", "price_test_pro")
    monkeypatch.setattr(billing, "STRIPE_WEBHOOK_SECRET", "whsec_dummy")
    monkeypatch.setattr(billing, "get_redis", lambda: fake_redis)

    app = FastAPI()
    app.include_router(billing.router)
    return TestClient(app, raise_server_exceptions=False), fake_redis


# ---------------------------------------------------------------------------
# /billing/create-checkout
# ---------------------------------------------------------------------------


class TestCreateCheckout:
    def test_returns_session_url_and_id(self, client, stripe_module_mock):
        tc, _redis = client
        res = tc.post("/billing/create-checkout", json={})
        assert res.status_code == 200
        body = res.json()
        assert body["checkout_url"].startswith("https://checkout.stripe.com")
        assert body["session_id"] == "cs_test_xyz"

    def test_503_when_secret_key_unset(self, monkeypatch, client):
        tc, _ = client
        import routers.billing as billing
        monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "")
        res = tc.post("/billing/create-checkout", json={})
        assert res.status_code == 503
        assert "Stripe is not configured" in res.json()["detail"]

    def test_503_when_price_id_unset(self, monkeypatch, client):
        tc, _ = client
        import routers.billing as billing
        monkeypatch.setattr(billing, "STRIPE_PRICE_ID_PRO", "")
        res = tc.post("/billing/create-checkout", json={})
        assert res.status_code == 503
        assert "Pro tier pricing not configured" in res.json()["detail"]

    def test_passes_through_custom_urls(self, client, stripe_module_mock):
        tc, _ = client
        tc.post("/billing/create-checkout", json={
            "success_url": "https://app.example.com/ok",
            "cancel_url": "https://app.example.com/no",
        })
        kwargs = stripe_module_mock.checkout.Session.create.call_args.kwargs
        assert kwargs["success_url"] == "https://app.example.com/ok"
        assert kwargs["cancel_url"] == "https://app.example.com/no"


# ---------------------------------------------------------------------------
# /billing/webhook
# ---------------------------------------------------------------------------


class TestWebhook:
    def _post(self, tc):
        return tc.post(
            "/billing/webhook",
            content=b'{"x":"y"}',
            headers={"stripe-signature": "t=123,v1=abc"},
        )

    def test_invalid_signature_returns_400(self, client, stripe_module_mock):
        tc, _ = client
        stripe_module_mock.Webhook.construct_event.side_effect = ValueError(
            "bad signature"
        )
        res = self._post(tc)
        assert res.status_code == 400
        assert "Invalid webhook signature" in res.json()["detail"]

    def test_503_when_webhook_secret_unset(self, monkeypatch, client):
        tc, _ = client
        import routers.billing as billing
        monkeypatch.setattr(billing, "STRIPE_WEBHOOK_SECRET", "")
        res = self._post(tc)
        assert res.status_code == 503

    def test_checkout_completed_activates_pro(self, client, stripe_module_mock):
        tc, redis = client
        stripe_module_mock.Webhook.construct_event.return_value = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_xyz"}},
        }
        res = self._post(tc)
        assert res.status_code == 200
        status = json.loads(redis._state["cerid:license:status"])
        assert status["active"] is True
        assert status["tier"] == "pro"
        assert status["source"] == "stripe"
        assert status["reference"] == "cs_xyz"

    def test_invoice_payment_subscription_create_activates(
        self, client, stripe_module_mock
    ):
        tc, redis = client
        stripe_module_mock.Webhook.construct_event.return_value = {
            "type": "invoice.payment_succeeded",
            "data": {"object": {
                "id": "in_xyz",
                "billing_reason": "subscription_create",
            }},
        }
        res = self._post(tc)
        assert res.status_code == 200
        status = json.loads(redis._state["cerid:license:status"])
        assert status["source"] == "stripe_invoice"
        assert status["reference"] == "in_xyz"

    def test_invoice_payment_other_reason_no_activation(
        self, client, stripe_module_mock
    ):
        tc, redis = client
        stripe_module_mock.Webhook.construct_event.return_value = {
            "type": "invoice.payment_succeeded",
            "data": {"object": {
                "id": "in_xyz",
                "billing_reason": "subscription_cycle",  # renewal — already active
            }},
        }
        res = self._post(tc)
        assert res.status_code == 200
        # No status written — billing_reason filter prevents redundant writes
        assert "cerid:license:status" not in redis._state

    def test_subscription_deleted_deactivates(
        self, client, stripe_module_mock, fake_redis
    ):
        # Pre-seed an active license
        fake_redis._state["cerid:license:tier"] = b"pro"
        fake_redis._state["cerid:license:status"] = json.dumps({"active": True, "tier": "pro"})

        tc, redis = client
        stripe_module_mock.Webhook.construct_event.return_value = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_xyz"}},
        }
        res = self._post(tc)
        assert res.status_code == 200
        assert "cerid:license:tier" not in redis._state
        assert "cerid:license:status" not in redis._state

    @pytest.mark.parametrize("status", ["past_due", "unpaid", "canceled", "incomplete_expired"])
    def test_subscription_updated_non_paying_deactivates(
        self, client, stripe_module_mock, fake_redis, status
    ):
        fake_redis._state["cerid:license:tier"] = b"pro"
        fake_redis._state["cerid:license:status"] = json.dumps({"active": True, "tier": "pro"})

        tc, redis = client
        stripe_module_mock.Webhook.construct_event.return_value = {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_xyz", "status": status}},
        }
        res = self._post(tc)
        assert res.status_code == 200
        assert "cerid:license:tier" not in redis._state, (
            f"status={status} should have deactivated entitlement"
        )

    @pytest.mark.parametrize("status", ["active", "trialing", "paused"])
    def test_subscription_updated_paying_states_noop(
        self, client, stripe_module_mock, fake_redis, status
    ):
        # Active license should remain
        fake_redis._state["cerid:license:tier"] = b"pro"

        tc, redis = client
        stripe_module_mock.Webhook.construct_event.return_value = {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_xyz", "status": status}},
        }
        res = self._post(tc)
        assert res.status_code == 200
        assert redis._state.get("cerid:license:tier") == b"pro", (
            f"status={status} should not have changed entitlement"
        )

    def test_unknown_event_type_acks_without_action(
        self, client, stripe_module_mock, fake_redis
    ):
        tc, redis = client
        stripe_module_mock.Webhook.construct_event.return_value = {
            "type": "customer.created",
            "data": {"object": {"id": "cus_xyz"}},
        }
        res = self._post(tc)
        assert res.status_code == 200
        assert res.json() == {"received": True}
        # No mutation
        assert "cerid:license:tier" not in redis._state


# ---------------------------------------------------------------------------
# /billing/status
# ---------------------------------------------------------------------------


class TestBillingStatus:
    def test_default_returns_community(self, client):
        tc, _ = client
        res = tc.get("/billing/status")
        assert res.status_code == 200
        body = res.json()
        assert body["active"] is False
        assert body["tier"] == "community"

    def test_returns_pro_when_active(self, client, fake_redis):
        fake_redis._state["cerid:license:status"] = json.dumps({
            "active": True,
            "tier": "pro",
            "source": "stripe",
            "reference": "cs_xyz",
            "activated_at": time.time(),
        })
        tc, _ = client
        res = tc.get("/billing/status")
        body = res.json()
        assert body["active"] is True
        assert body["tier"] == "pro"

    def test_includes_masked_key_when_present(self, client, fake_redis):
        fake_redis._state["cerid:license:key"] = "CERID-PRO-AAAA-BBBB-CCCC-DDDD-1234"
        tc, _ = client
        res = tc.get("/billing/status")
        body = res.json()
        assert "key_masked" in body
        assert "CERID-PRO" in body["key_masked"]
        assert "1234" in body["key_masked"]


# ---------------------------------------------------------------------------
# /billing/license (DELETE)
# ---------------------------------------------------------------------------


class TestDeactivate:
    def test_clears_redis_state(self, client, fake_redis):
        fake_redis._state["cerid:license:tier"] = b"pro"
        fake_redis._state["cerid:license:status"] = json.dumps({"active": True})
        tc, redis = client
        res = tc.delete("/billing/license")
        assert res.status_code == 200
        assert res.json() == {"status": "deactivated", "tier": "community"}
        assert "cerid:license:tier" not in redis._state
        assert "cerid:license:status" not in redis._state


# ---------------------------------------------------------------------------
# /billing/waitlist
# ---------------------------------------------------------------------------


class TestWaitlist:
    def test_join_returns_position(self, client):
        tc, _ = client
        res = tc.post("/billing/waitlist", json={"email": "alice@example.com"})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "joined"
        assert body["email"] == "alice@example.com"
        assert body["position"] == 1

    def test_invalid_email_400(self, client):
        tc, _ = client
        res = tc.post("/billing/waitlist", json={"email": "not-an-email"})
        assert res.status_code == 400

    def test_count_endpoint(self, client):
        tc, _ = client
        tc.post("/billing/waitlist", json={"email": "a@example.com"})
        tc.post("/billing/waitlist", json={"email": "b@example.com"})
        res = tc.get("/billing/waitlist/count")
        assert res.json() == {"count": 2}


# ---------------------------------------------------------------------------
# /billing/validate-key
# ---------------------------------------------------------------------------


class TestValidateKey:
    def test_invalid_format_400(self, client):
        tc, _ = client
        res = tc.post("/billing/validate-key", json={"key": "not-a-key"})
        assert res.status_code == 400

    # Canonical format: CERID-PRO-XXXX-XXXX-XXXX-XXXX-XXXX (5 hex groups of 4).
    # Constructed by hand instead of via generate_license_key() because the
    # generator requires CERID_LICENSE_SECRET to be set, and one of the tests
    # specifically exercises the no-secret branch.
    _VALID_FORMAT_KEY = "CERID-PRO-AAAA-BBBB-CCCC-DDDD-1234"

    def test_format_valid_no_secret_activates(self, monkeypatch, client):
        """When CERID_LICENSE_SECRET is unset, format-valid keys are accepted
        (dev/preview mode)."""
        import routers.billing as billing
        monkeypatch.setattr(billing, "LICENSE_SECRET", "")

        tc, redis = client
        res = tc.post(
            "/billing/validate-key",
            json={"key": self._VALID_FORMAT_KEY},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is True
        assert body["tier"] == "pro"
        # License-key Redis entry persisted for /billing/status reflection
        assert redis._state.get("cerid:license:key") == self._VALID_FORMAT_KEY

    def test_hmac_mismatch_when_secret_set(self, monkeypatch, client):
        """When CERID_LICENSE_SECRET is set, format-valid keys without a
        matching HMAC signature must be rejected. A hand-rolled valid-format
        key won't match any real secret's HMAC."""
        import routers.billing as billing
        monkeypatch.setattr(billing, "LICENSE_SECRET", "test-secret")

        tc, _ = client
        res = tc.post(
            "/billing/validate-key",
            json={"key": self._VALID_FORMAT_KEY},
        )
        assert res.status_code == 400
        assert "Invalid or expired" in res.json()["detail"]
