# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Billing endpoints — Stripe integration for Pro tier licensing."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from deps import get_redis
from errors import CeridError
from utils.license import mask_license_key
from utils.license import validate_license_key as validate_key_format

router = APIRouter()
logger = logging.getLogger("ai-companion.billing")

# Stripe config from env
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO", "")
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "http://localhost:3000/settings?billing=success")
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "http://localhost:3000/settings?billing=cancel")

# HMAC secret for offline license key validation
LICENSE_SECRET = os.getenv("CERID_LICENSE_SECRET", "")

# Redis keys for license management
_LICENSE_KEY = "cerid:license:key"
_LICENSE_TIER = "cerid:license:tier"
_LICENSE_EXPIRES = "cerid:license:expires"
_LICENSE_STATUS = "cerid:license:status"
_WAITLIST_KEY = "cerid:waitlist"


def _get_stripe():
    """Lazy-import stripe with clear error message."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured. Set STRIPE_SECRET_KEY env var.",
        )
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        return stripe
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Stripe SDK not installed. Install with: pip install stripe>=8.0",
        )


class CheckoutRequest(BaseModel):
    """Request body for creating a Stripe Checkout session."""
    success_url: str | None = None
    cancel_url: str | None = None


class LicenseKeyRequest(BaseModel):
    """Request body for manual license key validation."""
    key: str


@router.post("/billing/create-checkout")
async def create_checkout(req: CheckoutRequest):
    """Create a Stripe Checkout session for Pro tier upgrade."""
    stripe = _get_stripe()

    if not STRIPE_PRICE_ID_PRO:
        raise HTTPException(
            status_code=503,
            detail="Pro tier pricing not configured. Set STRIPE_PRICE_ID_PRO env var.",
        )

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID_PRO, "quantity": 1}],
            success_url=req.success_url or STRIPE_SUCCESS_URL,
            cancel_url=req.cancel_url or STRIPE_CANCEL_URL,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error("Failed to create Stripe Checkout session: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events (payment confirmation)."""
    stripe = _get_stripe()

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Stripe webhook verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    redis = get_redis()

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        _activate_pro_license(redis, source="stripe", reference=session.get("id", ""))
        logger.info("Pro license activated via Stripe Checkout: %s", session.get("id"))

    elif event["type"] == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        if invoice.get("billing_reason") == "subscription_create":
            _activate_pro_license(redis, source="stripe_invoice", reference=invoice.get("id", ""))
            logger.info("Pro license confirmed via invoice payment: %s", invoice.get("id"))

    elif event["type"] == "customer.subscription.deleted":
        _deactivate_license(redis)
        logger.info("Pro license deactivated — subscription cancelled")

    return JSONResponse(status_code=200, content={"received": True})


@router.get("/billing/status")
async def billing_status():
    """Return current license/subscription status."""
    redis = get_redis()
    status = _get_license_status(redis)
    # Include masked key if stored
    raw_key = redis.get(_LICENSE_KEY)
    if raw_key:
        key_str = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
        status["key_masked"] = mask_license_key(key_str)
    return status


@router.delete("/billing/license")
async def deactivate_license():
    """Deactivate license, revert to Community tier."""
    redis = get_redis()
    _deactivate_license(redis)
    logger.info("License deactivated via API")
    return {"status": "deactivated", "tier": "community"}


class WaitlistRequest(BaseModel):
    """Request body for Pro waitlist signup."""
    email: str


@router.post("/billing/waitlist")
async def join_waitlist(req: WaitlistRequest):
    """Add email to Pro waitlist (interim before Stripe)."""
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address.")
    redis = get_redis()
    redis.sadd(_WAITLIST_KEY, email)
    count = redis.scard(_WAITLIST_KEY)
    logger.info("Waitlist signup: %s (total: %d)", email, count)
    return {"status": "joined", "email": email, "position": count}


@router.get("/billing/waitlist/count")
async def waitlist_count():
    """Return current waitlist count."""
    redis = get_redis()
    count = redis.scard(_WAITLIST_KEY)
    return {"count": count}


def _validate_license_key(key: str) -> bool:
    """Validate license key using HMAC-SHA256 signature."""
    if not LICENSE_SECRET:
        logger.warning("CERID_LICENSE_SECRET not set — license validation disabled")
        return False
    # Key format: CERID-PRO-{payload}-{signature}
    # payload = base64-encoded data, signature = HMAC-SHA256 of payload
    parts = key.split("-")
    if len(parts) < 4 or parts[0] != "CERID" or parts[1] != "PRO":
        return False
    payload = "-".join(parts[2:-1])
    signature = parts[-1]
    expected = hmac.new(
        LICENSE_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()[:16]
    return hmac.compare_digest(signature, expected)


@router.post("/billing/validate-key")
async def validate_license_key_endpoint(req: LicenseKeyRequest):
    """Validate a manually-entered license key for offline activation.

    Two-phase validation:
    1. Format check via ``utils.license.validate_license_key``
    2. HMAC signature check via ``_validate_license_key`` (when secret is set)
    """
    redis = get_redis()
    key = req.key.strip()

    # Step 1: format validation
    fmt_result = validate_key_format(key)
    if not fmt_result["valid"]:
        raise HTTPException(status_code=400, detail=fmt_result.get("error", "Invalid license key format."))

    # Step 2: HMAC validation (skipped if LICENSE_SECRET is not configured)
    if LICENSE_SECRET:
        if not _validate_license_key(key):
            raise HTTPException(status_code=400, detail="Invalid or expired license key.")
    # When LICENSE_SECRET is not set, accept format-valid keys (dev/preview mode)

    logger.info("License key activation attempt: %s...", key[:14])
    _activate_pro_license(redis, source="manual", reference=key)
    redis.set(_LICENSE_KEY, key)
    return {"valid": True, "tier": "pro", "message": "Pro license activated."}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _activate_pro_license(redis, source: str = "unknown", reference: str = "") -> None:
    """Activate Pro tier license in Redis."""
    redis.set(_LICENSE_TIER, "pro")
    redis.set(_LICENSE_STATUS, json.dumps({
        "active": True,
        "tier": "pro",
        "source": source,
        "reference": reference,
        "activated_at": time.time(),
    }))
    # Update feature tier at runtime
    try:
        import config.features as features_mod
        features_mod.FEATURE_TIER = "pro"
        features_mod._refresh_flags()
        logger.info("Feature tier elevated to 'pro' at runtime")
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Failed to update feature tier: %s", e)


def _deactivate_license(redis) -> None:
    """Revert to community tier."""
    redis.delete(_LICENSE_TIER, _LICENSE_STATUS, _LICENSE_KEY, _LICENSE_EXPIRES)
    try:
        import config.features as features_mod
        features_mod.FEATURE_TIER = "community"
        features_mod._refresh_flags()
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Failed to reset feature tier on license deactivation: %s", e)


def _get_license_status(redis) -> dict:
    """Read current license status from Redis."""
    status_raw = redis.get(_LICENSE_STATUS)
    if status_raw:
        try:
            return json.loads(status_raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Corrupt license status in Redis, falling back to tier key: %s", e)

    # Check if tier override is set
    tier = redis.get(_LICENSE_TIER)
    if tier:
        tier_str = tier.decode("utf-8") if isinstance(tier, bytes) else str(tier)
        if tier_str in ("pro", "enterprise"):
            return {"active": True, "tier": tier_str, "source": "env_override"}

    return {"active": False, "tier": "community", "source": "default"}
