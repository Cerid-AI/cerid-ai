# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Offline license activation + self-serve trial for self-hosted installs.

This is the community edition's entitlement surface. It holds no payment
code and talks to no payment provider: purchase happens on cerid.ai, which
issues an Ed25519-signed key, and this router accepts that key offline.

Endpoints (all local, no network):
    GET  /license/status      current entitlement + trial state
    POST /license/activate    { key } → validate + persist + elevate tier
    POST /license/deactivate  drop back to the configured baseline
    POST /license/trial       start the one-time 14-day trial (no card)

Relationship to the commercial build
------------------------------------
The commercial build ships a Stripe-backed router that owns the same Redis
keys. Both must never run at once or they would race each other's writes on
``cerid:license:status``, so ``main.py`` mounts this router only when that
one is absent. The key namespace is deliberately shared: a user who starts
on the community build and later moves to the desktop build keeps their
activation.

On enforcement
--------------
Self-hosted entitlement is an honor system and this code does not pretend
otherwise — the state lives in the user's own Redis, and the community
key validator is format-only by design (real signature verification is in
the commercial build). The trial is a friction remover for honest users,
not a lock. Anti-tamper here would cost real complexity and buy nothing
against anyone willing to edit their own database.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_redis
from utils.license import mask_license_key, validate_license_key

logger = logging.getLogger("ai-companion.license")
router = APIRouter(prefix="/license", tags=["license"])

# Shared with the commercial build's router — see module docstring.
_LICENSE_KEY = "cerid:license:key"
_LICENSE_TIER = "cerid:license:tier"
_LICENSE_EXPIRES = "cerid:license:expires"
_LICENSE_STATUS = "cerid:license:status"
_TRIAL_STARTED = "cerid:license:trial_started"
_TRIAL_EXPIRES = "cerid:license:trial_expires"

TRIAL_DAYS = int(os.getenv("CERID_TRIAL_DAYS", "14"))
TRIAL_TIER = "pro"
PURCHASE_URL = os.getenv("CERID_PURCHASE_URL", "https://cerid.ai/pricing")

_PAID_TIERS = ("pro", "enterprise")
_TIER_RANK = {"community": 0, "pro": 1, "enterprise": 2}


def higher_tier(a: str, b: str) -> str:
    """The more capable of two tier names; unrecognised names rank lowest.

    Entitlement sources compose by taking the maximum, never by overwriting:
    a Pro trial running on an operator's ``CERID_TIER=enterprise`` box must
    not knock that box down to Pro.
    """
    return a if _TIER_RANK.get(a, 0) >= _TIER_RANK.get(b, 0) else b


# --- Response models ---------------------------------------------------------

class TrialState(BaseModel):
    available: bool
    active: bool
    days_remaining: int | None = None
    expires_at: int | None = None


class LicenseStatusResponse(BaseModel):
    tier: str
    active: bool
    source: str
    key_masked: str | None = None
    expires_at: int | None = None
    trial: TrialState
    purchase_url: str


class ActivateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=512)


class DeactivateResponse(BaseModel):
    status: str
    tier: str


# --- Helpers -----------------------------------------------------------------

def _baseline_tier() -> str:
    """The tier floor from ``CERID_TIER`` — a license elevates above it.

    Deactivation and expiry fall back to *this*, never to a hardcoded
    ``community``: clobbering an operator's ``CERID_TIER=enterprise`` pin was
    a real bug once (see docs/CONVENTIONS.md — "env is the floor, dynamic
    state is the elevator").
    """
    return os.getenv("CERID_TIER", "community")


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value) if value is not None else ""


def _set_tier(tier: str) -> None:
    try:
        import config.features as features_mod
        features_mod.set_tier(tier)
    except Exception as exc:  # noqa: BLE001 — tier sync must never 500 a request
        logger.warning("Failed to set feature tier to %r: %s", tier, exc)


def trial_state(redis) -> TrialState:
    """Trial availability and remaining days, derived from the stored expiry."""
    started = redis.get(_TRIAL_STARTED)
    if not started:
        return TrialState(available=True, active=False)

    expires_raw = _decode(redis.get(_TRIAL_EXPIRES))
    try:
        expires_at = int(float(expires_raw))
    except (TypeError, ValueError):
        # Started-but-unparseable expiry: treat as consumed, not as an open
        # trial. Failing toward "already used" keeps a corrupt value from
        # minting an unlimited trial.
        return TrialState(available=False, active=False)

    now = int(time.time())
    if now >= expires_at:
        return TrialState(available=False, active=False, expires_at=expires_at)

    remaining_days = max(1, (expires_at - now + 86399) // 86400)
    return TrialState(
        available=False, active=True,
        days_remaining=int(remaining_days), expires_at=expires_at,
    )


# Entitlement provenance, as the UI needs to reason about it. Distinct from the
# tier: "what am I entitled to" and "why" drive different affordances.
STATE_COMMUNITY = "community"        # free tier, trial still on the table
STATE_TRIAL = "trial"                # trial running
STATE_TRIAL_EXPIRED = "trial_expired"  # trial used up, back on the free tier
STATE_LICENSED = "licensed"          # a real key is active
STATE_UNLICENSED_PRO = "unlicensed_pro"  # paid tier from CERID_TIER, no key, no trial


def entitlement_state(redis) -> str:
    """Classify *why* this server has the tier it has.

    ``unlicensed_pro`` is the case worth naming: an operator pinned
    ``CERID_TIER`` to a paid tier without ever activating a license. That is a
    legitimate configuration for air-gapped and enterprise images, and it is
    also exactly how someone runs the paid features without paying — the code
    cannot tell those apart, so it does not try to. It reports the fact and
    lets the UI say so plainly.
    """
    from utils.license import verification_enabled

    status = _stored_status(redis)
    has_key = (
        bool(status.get("active"))
        and status.get("tier") in _PAID_TIERS
        and not _key_expired(status)
    )
    # A stored key only proves anything if signatures are actually checked.
    # With CERID_LICENSE_PUBLIC_KEY blanked, any shaped string activates — so
    # the server cannot honestly claim to be licensed, and saying otherwise
    # would make disabling verification the quietest way to run unlicensed.
    if has_key and verification_enabled():
        return STATE_LICENSED
    if trial_state(redis).active:
        return STATE_TRIAL
    if has_key or _baseline_tier() in _PAID_TIERS:
        return STATE_UNLICENSED_PRO
    if not trial_state(redis).available:
        return STATE_TRIAL_EXPIRED
    return STATE_COMMUNITY


UNLICENSED_WATERMARK = (
    "Generated by an unlicensed copy of Cerid Pro — https://cerid.ai/pricing"
)


def current_license_watermark() -> str:
    """Watermark for Pro-generated artifacts, or ``""`` when properly licensed.

    App-layer helper so ``core/`` never has to reach for entitlement state (it
    may not import ``app``). Callers thread the result into the generator.
    Returns ``""`` on any failure — a marking feature must never break the
    artifact it marks.
    """
    try:
        if entitlement_state(get_redis()) == STATE_UNLICENSED_PRO:
            return UNLICENSED_WATERMARK
    except Exception as exc:  # noqa: BLE001 — never fail generation over a notice
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error('app.routers.license.watermark', exc)
    return ""


def active_trial_tier(redis) -> str | None:
    """The tier an in-flight trial grants, or ``None`` if no trial is running.

    Public helper: the commercial build's Stripe router calls this so its own
    reconcile doesn't reset an active trial back to the baseline. One trial
    implementation, two thin entry points.
    """
    return TRIAL_TIER if trial_state(redis).active else None


def begin_trial(redis) -> TrialState:
    """Start the one-time trial. Raises ``HTTPException`` if unavailable."""
    trial = trial_state(redis)
    if not trial.available:
        detail = (
            "Trial already running."
            if trial.active
            else "The trial for this installation has already been used."
        )
        raise HTTPException(status_code=409, detail=detail)

    now = int(time.time())
    expires_at = now + TRIAL_DAYS * 86400
    # Write the expiry before the start marker: if the process dies between
    # the two writes, the next call sees no start marker and the trial is
    # still available — the failure mode is a re-offered trial, not a
    # consumed-but-inactive one.
    redis.set(_TRIAL_EXPIRES, str(expires_at))
    redis.set(_TRIAL_STARTED, str(now))
    logger.info("Pro trial started — %d days, expires %d", TRIAL_DAYS, expires_at)
    return trial_state(redis)


def _stored_status(redis) -> dict:
    raw = redis.get(_LICENSE_STATUS)
    if not raw:
        return {}
    try:
        return json.loads(_decode(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Corrupt license status in Redis: %s", exc)
        return {}


def _key_expired(status: dict) -> bool:
    expires_at = status.get("expires_at")
    if not expires_at:
        return False  # perpetual key (or none recorded) — never locally expired
    try:
        return int(time.time()) > int(expires_at)
    except (TypeError, ValueError):
        # A corrupt expiry must not silently grant a perpetual license.
        logger.warning("Unparseable license expiry %r — treating as expired", expires_at)
        return True


def reconcile_license_state(redis) -> str:
    """Re-derive the runtime tier from persisted state. Safe to call repeatedly.

    Precedence: a valid unexpired key beats an active trial, which beats the
    ``CERID_TIER`` baseline. Called at startup (persisted entitlement must
    survive a restart) and after every mutation.
    """
    baseline = _baseline_tier()

    status = _stored_status(redis)
    if status.get("active") and status.get("tier") in _PAID_TIERS:
        if _key_expired(status):
            logger.info("License expired — reverting to baseline tier %r", baseline)
            _clear_license(redis)
        else:
            target = higher_tier(str(status["tier"]), baseline)
            _set_tier(target)
            return target

    if trial_state(redis).active:
        target = higher_tier(TRIAL_TIER, baseline)
        _set_tier(target)
        return target

    _set_tier(baseline)
    return baseline


def _clear_license(redis) -> None:
    redis.delete(_LICENSE_KEY, _LICENSE_TIER, _LICENSE_EXPIRES, _LICENSE_STATUS)


# --- Endpoints ---------------------------------------------------------------

@router.get("/status", response_model=LicenseStatusResponse)
async def license_status() -> LicenseStatusResponse:
    """Current entitlement, its provenance, and trial availability."""
    redis = get_redis()
    tier = reconcile_license_state(redis)
    status = _stored_status(redis)
    trial = trial_state(redis)

    if status.get("active") and not _key_expired(status):
        source = status.get("source") or "license_key"
        key_masked = mask_license_key(_decode(redis.get(_LICENSE_KEY))) or None
        expires_at = status.get("expires_at")
    elif trial.active:
        source, key_masked, expires_at = "trial", None, trial.expires_at
    elif tier in _PAID_TIERS:
        source, key_masked, expires_at = "env_override", None, None
    else:
        source, key_masked, expires_at = "default", None, None

    return LicenseStatusResponse(
        tier=tier,
        active=tier in _PAID_TIERS,
        source=source,
        key_masked=key_masked,
        expires_at=expires_at,
        trial=trial,
        purchase_url=PURCHASE_URL,
    )


@router.get("/capabilities")  # response-model-allowed: mirrors get_feature_status()
async def license_capabilities() -> dict:
    """Per-feature entitlement map for the UI's lock affordances.

    Same shape the commercial build serves, so one client works against both.
    Without this the community build had no capabilities source at all: every
    Pro surface rendered locked even after a customer activated a real key.
    """
    redis = get_redis()
    reconcile_license_state(redis)

    import config.features as features_mod

    return {**features_mod.get_feature_status(), "license_state": entitlement_state(redis)}


@router.post("/activate", response_model=LicenseStatusResponse)
async def activate_license(req: ActivateRequest) -> LicenseStatusResponse:
    """Validate and persist a license key bought at cerid.ai."""
    key = req.key.strip()
    result = validate_license_key(key)
    if not result.get("valid"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "Invalid or expired license key.",
        )

    tier = str(result.get("tier") or "pro")
    if tier not in _PAID_TIERS:
        raise HTTPException(status_code=400, detail=f"Key grants no paid tier (got {tier!r}).")

    # The community validator reports no expiry; the commercial one does.
    expires_at = result.get("expires_at")

    redis = get_redis()
    redis.set(_LICENSE_KEY, key)
    redis.set(_LICENSE_TIER, tier)
    redis.set(_LICENSE_STATUS, json.dumps({
        "active": True,
        "tier": tier,
        "source": "license_key",
        "activated_at": time.time(),
        "expires_at": expires_at,
    }))
    if expires_at:
        redis.set(_LICENSE_EXPIRES, str(expires_at))
    else:
        redis.delete(_LICENSE_EXPIRES)

    reconcile_license_state(redis)
    logger.info("License activated — tier %r", tier)
    return await license_status()


@router.post("/deactivate", response_model=DeactivateResponse)
async def deactivate_license() -> DeactivateResponse:
    """Remove the stored key and fall back to the baseline tier."""
    redis = get_redis()
    _clear_license(redis)
    tier = reconcile_license_state(redis)
    logger.info("License deactivated — tier now %r", tier)
    return DeactivateResponse(status="deactivated", tier=tier)


@router.post("/trial", response_model=LicenseStatusResponse)
async def start_trial() -> LicenseStatusResponse:
    """Start the one-time, no-card Pro trial."""
    redis = get_redis()
    begin_trial(redis)
    reconcile_license_state(redis)
    return await license_status()
