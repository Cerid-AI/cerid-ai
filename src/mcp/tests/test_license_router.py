# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the community-edition license/trial router (app/routers/license.py).

This router is the open-core conversion path: it is what a self-hosted user
touches to start a trial or activate a key bought on cerid.ai. It ships in the
public tree, where the key validator is format-only by design, so these tests
pin *entitlement composition* — which source wins, and what a restart restores
— rather than cryptography.
"""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import license as lic


def _well_formed_key() -> str:
    """A structurally valid key blob.

    Only its *shape* matters here. Whether a key is cryptographically genuine
    is the validator's question, and the two editions answer it differently
    (the commercial build verifies an Ed25519 signature; the community build
    checks format only). This file syncs to both trees, so the activation
    tests stub the validator and pin what the *router* does with its verdict —
    persist, elevate, mask, compose. ``utils.license`` has its own tests.
    """
    body = base64.b32encode(b"\x01" * 72).decode().rstrip("=")
    groups = "-".join(body[i:i + 4] for i in range(0, len(body), 4))
    return f"CERID-PRO-{groups}"


@pytest.fixture
def accepts_keys(monkeypatch):
    """Make the validator accept ``_well_formed_key()`` as a perpetual Pro key."""
    monkeypatch.setattr(
        lic, "validate_license_key",
        lambda key: (
            {"valid": True, "tier": "pro", "expires_at": None, "error": None}
            if key == _well_formed_key()
            else {"valid": False, "tier": "community", "error": "Invalid key format"}
        ),
    )


@pytest.fixture(autouse=True)
def _restore_feature_tier():
    """Tier is module-level global state; leaking it breaks unrelated tests."""
    import config.features as features_mod

    original = features_mod.FEATURE_TIER
    yield
    features_mod.FEATURE_TIER = original
    features_mod._refresh_flags()


@pytest.fixture
def fake_redis():
    """In-memory stand-in for the get/set/delete subset the router uses."""
    state: dict = {}
    fake = MagicMock()
    fake.get.side_effect = state.get
    fake.set.side_effect = lambda k, v, **kw: state.__setitem__(k, v) or True
    fake.delete.side_effect = lambda *keys: [state.pop(k, None) for k in keys]
    fake._state = state
    return fake


@pytest.fixture
def client(fake_redis, monkeypatch):
    monkeypatch.setattr(lic, "get_redis", lambda: fake_redis)
    monkeypatch.delenv("CERID_TIER", raising=False)
    app = FastAPI()
    app.include_router(lic.router)
    return TestClient(app)


# --- Default state -----------------------------------------------------------

def test_fresh_install_is_community_with_trial_offered(client):
    body = client.get("/license/status").json()
    assert body["tier"] == "community"
    assert body["active"] is False
    assert body["trial"] == {
        "available": True, "active": False,
        "days_remaining": None, "expires_at": None,
    }
    # The purchase URL is the whole point of the pane — it must be present and
    # must not be the /pro path that 404'd through v1.0.1.
    assert body["purchase_url"] == "https://cerid.ai/pricing"


# --- Key activation ----------------------------------------------------------

def test_malformed_key_is_rejected(client):
    res = client.post("/license/activate", json={"key": "not-a-key"})
    assert res.status_code == 400
    assert client.get("/license/status").json()["tier"] == "community"


def test_activation_elevates_tier_and_masks_the_key(client, accepts_keys):
    res = client.post("/license/activate", json={"key": _well_formed_key()})
    assert res.status_code == 200
    body = res.json()
    assert body["tier"] == "pro"
    assert body["active"] is True
    assert body["source"] == "license_key"
    # The raw key must never come back out of the API.
    assert body["key_masked"].startswith("CERID-PRO-****")
    assert _well_formed_key() not in json.dumps(body)


def test_masking_never_echoes_a_key_back(client, accepts_keys, monkeypatch):
    """An ungrouped key body must not round-trip through the API in the clear."""
    ungrouped = "CERID-PRO-" + base64.b32encode(b"\x02" * 72).decode().rstrip("=")
    monkeypatch.setattr(
        lic, "validate_license_key",
        lambda key: {"valid": True, "tier": "pro", "expires_at": None, "error": None},
    )
    body = client.post("/license/activate", json={"key": ungrouped}).json()
    assert ungrouped not in json.dumps(body)
    assert "****" in body["key_masked"]


def test_deactivation_returns_to_community(client, accepts_keys):
    client.post("/license/activate", json={"key": _well_formed_key()})
    assert client.post("/license/deactivate").json()["tier"] == "community"
    assert client.get("/license/status").json()["active"] is False


def test_expired_key_does_not_grant_pro(client, fake_redis):
    fake_redis._state[lic._LICENSE_STATUS] = json.dumps({
        "active": True, "tier": "pro", "source": "license_key",
        "expires_at": int(time.time()) - 60,
    })
    assert client.get("/license/status").json()["tier"] == "community"


def test_corrupt_key_expiry_is_treated_as_expired(client, fake_redis):
    """Fail closed: garbage in the expiry field must not mint a perpetual key."""
    fake_redis._state[lic._LICENSE_STATUS] = json.dumps({
        "active": True, "tier": "pro", "source": "license_key",
        "expires_at": "whenever",
    })
    assert client.get("/license/status").json()["tier"] == "community"


def test_perpetual_key_never_expires(client, fake_redis):
    fake_redis._state[lic._LICENSE_STATUS] = json.dumps({
        "active": True, "tier": "pro", "source": "license_key", "expires_at": None,
    })
    assert client.get("/license/status").json()["tier"] == "pro"


# --- Trial -------------------------------------------------------------------

def test_trial_grants_pro_and_reports_remaining_days(client):
    body = client.post("/license/trial").json()
    assert body["tier"] == "pro"
    assert body["source"] == "trial"
    assert body["trial"]["active"] is True
    assert body["trial"]["days_remaining"] == lic.TRIAL_DAYS
    assert body["trial"]["available"] is False


def test_trial_cannot_be_started_twice(client):
    client.post("/license/trial")
    assert client.post("/license/trial").status_code == 409


def test_expired_trial_reverts_to_community_and_is_not_reoffered(client, fake_redis):
    past = int(time.time()) - 1
    fake_redis._state[lic._TRIAL_STARTED] = str(past - 100)
    fake_redis._state[lic._TRIAL_EXPIRES] = str(past)

    body = client.get("/license/status").json()
    assert body["tier"] == "community"
    assert body["trial"]["active"] is False
    # Consumed, not renewable — otherwise the trial is unlimited.
    assert body["trial"]["available"] is False
    assert client.post("/license/trial").status_code == 409


def test_corrupt_trial_expiry_is_treated_as_consumed(client, fake_redis):
    """Failing toward 'used' — a garbage value must not mint a fresh trial."""
    fake_redis._state[lic._TRIAL_STARTED] = "123"
    fake_redis._state[lic._TRIAL_EXPIRES] = "not-a-number"
    assert client.get("/license/status").json()["trial"]["available"] is False


def test_purchased_key_supersedes_a_running_trial(client, accepts_keys):
    client.post("/license/trial")
    body = client.post("/license/activate", json={"key": _well_formed_key()}).json()
    assert body["source"] == "license_key"
    assert body["tier"] == "pro"


# --- Composition with the CERID_TIER baseline --------------------------------

def test_trial_never_downgrades_an_operator_tier_pin(client, monkeypatch):
    """A Pro trial on a CERID_TIER=enterprise box must not drop it to Pro."""
    monkeypatch.setenv("CERID_TIER", "enterprise")
    assert client.post("/license/trial").json()["tier"] == "enterprise"


def test_deactivate_falls_back_to_the_baseline_not_to_community(client, accepts_keys, monkeypatch):
    monkeypatch.setenv("CERID_TIER", "enterprise")
    client.post("/license/activate", json={"key": _well_formed_key()})
    assert client.post("/license/deactivate").json()["tier"] == "enterprise"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("pro", "community", "pro"),
        ("community", "enterprise", "enterprise"),
        ("pro", "enterprise", "enterprise"),
        ("pro", "pro", "pro"),
        ("pro", "nonsense", "pro"),  # unknown names rank lowest
    ],
)
def test_higher_tier_picks_the_more_capable_tier(a, b, expected):
    assert lic.higher_tier(a, b) == expected


# --- Entitlement provenance --------------------------------------------------
#
# These five states drive the unlicensed/expired affordances in the UI. The one
# that matters commercially is unlicensed_pro: paid features running with no
# license and no trial.

def test_state_community_on_a_fresh_install(client, fake_redis):
    assert lic.entitlement_state(fake_redis) == lic.STATE_COMMUNITY


def test_state_trial_while_running(client, fake_redis):
    client.post("/license/trial")
    assert lic.entitlement_state(fake_redis) == lic.STATE_TRIAL


def test_state_trial_expired_after_it_lapses(client, fake_redis):
    past = int(time.time()) - 1
    fake_redis._state[lic._TRIAL_STARTED] = str(past - 100)
    fake_redis._state[lic._TRIAL_EXPIRES] = str(past)
    assert lic.entitlement_state(fake_redis) == lic.STATE_TRIAL_EXPIRED


def test_state_licensed_with_a_key(client, fake_redis, accepts_keys):
    client.post("/license/activate", json={"key": _well_formed_key()})
    assert lic.entitlement_state(fake_redis) == lic.STATE_LICENSED


def test_state_unlicensed_pro_when_cerid_tier_grants_paid_features(
    client, fake_redis, monkeypatch,
):
    monkeypatch.setenv("CERID_TIER", "pro")
    assert lic.entitlement_state(fake_redis) == lic.STATE_UNLICENSED_PRO


def test_a_real_license_is_not_reported_as_unlicensed(
    client, fake_redis, accepts_keys, monkeypatch,
):
    """A paying customer who ALSO pins CERID_TIER must not be shamed for it."""
    monkeypatch.setenv("CERID_TIER", "pro")
    client.post("/license/activate", json={"key": _well_formed_key()})
    assert lic.entitlement_state(fake_redis) == lic.STATE_LICENSED


def test_a_running_trial_outranks_the_env_pin(client, fake_redis, monkeypatch):
    """Someone mid-trial is converting, not bypassing — no unlicensed warning."""
    monkeypatch.setenv("CERID_TIER", "pro")
    client.post("/license/trial")
    assert lic.entitlement_state(fake_redis) == lic.STATE_TRIAL


def test_an_unverifiable_license_is_not_reported_as_licensed(
    client, fake_redis, accepts_keys, monkeypatch,
):
    """Blanking the verify key must not become the quietest way to run unlicensed.

    With verification off, any shaped string activates — so a stored key proves
    nothing and the server must not claim to be licensed on the strength of it.
    """
    client.post("/license/activate", json={"key": _well_formed_key()})
    assert lic.entitlement_state(fake_redis) == lic.STATE_LICENSED

    import utils.license as ul

    monkeypatch.setattr(ul, "verification_enabled", lambda: False)
    assert lic.entitlement_state(fake_redis) == lic.STATE_UNLICENSED_PRO


def test_capabilities_reports_the_state(client, monkeypatch):
    monkeypatch.setenv("CERID_TIER", "pro")
    body = client.get("/license/capabilities").json()
    assert body["license_state"] == lic.STATE_UNLICENSED_PRO
    assert "features" in body  # still the full capability map


# --- Artifact watermark ------------------------------------------------------

def test_watermark_marks_pro_output_on_an_unlicensed_install(
    client, fake_redis, monkeypatch,
):
    monkeypatch.setenv("CERID_TIER", "pro")
    monkeypatch.setattr(lic, "get_redis", lambda: fake_redis)
    assert lic.current_license_watermark() == lic.UNLICENSED_WATERMARK
    assert "cerid.ai/pricing" in lic.current_license_watermark()


def test_watermark_is_empty_for_a_licensed_install(client, fake_redis, accepts_keys, monkeypatch):
    monkeypatch.setattr(lic, "get_redis", lambda: fake_redis)
    client.post("/license/activate", json={"key": _well_formed_key()})
    assert lic.current_license_watermark() == ""


def test_watermark_is_empty_for_a_plain_community_install(client, fake_redis, monkeypatch):
    monkeypatch.setattr(lic, "get_redis", lambda: fake_redis)
    assert lic.current_license_watermark() == ""


def test_watermark_never_raises_when_redis_is_down(monkeypatch):
    """A marking feature must not break the artifact it marks."""
    def _boom():
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(lic, "get_redis", _boom)
    assert lic.current_license_watermark() == ""


def test_digest_carries_the_notice_into_its_payload():
    """The watermark has to survive serialisation to be worth anything."""
    from core.agents.daily_digest import DigestResult

    payload = DigestResult(license_notice=lic.UNLICENSED_WATERMARK).to_dict()
    assert payload["license_notice"] == lic.UNLICENSED_WATERMARK


# --- Restart durability ------------------------------------------------------

def test_reconcile_restores_an_activated_tier_after_restart(client, accepts_keys, fake_redis):
    """FEATURE_TIER is read from env at import; without the startup reconcile
    an activated license silently evaporates on every container restart."""
    import config.features as features_mod

    client.post("/license/activate", json={"key": _well_formed_key()})
    features_mod.set_tier("community")  # simulate a fresh process

    assert lic.reconcile_license_state(fake_redis) == "pro"
    assert features_mod.FEATURE_TIER == "pro"


def test_reconcile_restores_a_running_trial_after_restart(client, fake_redis):
    import config.features as features_mod

    client.post("/license/trial")
    features_mod.set_tier("community")

    assert lic.reconcile_license_state(fake_redis) == "pro"
