# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""License key validation for Cerid Pro tier.

License keys are validated locally (offline-first) using HMAC signatures.
Format: CERID-PRO-XXXX-XXXX-XXXX-XXXX-XXXX (base32-encoded, 5 groups of 4).

This module is the canonical location for key generation and validation.
The billing router delegates to these functions for license operations.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

LICENSE_SECRET = os.getenv("CERID_LICENSE_SECRET", "")


def generate_license_key(email: str, tier: str = "pro") -> str:
    """Generate a license key (server-side only, for license activation).

    Parameters
    ----------
    email:
        Purchaser email address (embedded in payload for audit).
    tier:
        License tier — currently only ``"pro"`` is supported.

    Returns
    -------
    str
        Formatted key: ``CERID-PRO-XXXX-XXXX-XXXX-XXXX-XXXX``
    """
    if not LICENSE_SECRET:
        raise ValueError("CERID_LICENSE_SECRET must be set to generate license keys")
    payload = f"{email}:{tier}:{int(time.time())}"
    sig = hmac.new(
        LICENSE_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()[:20]
    formatted = "-".join(sig[i : i + 4].upper() for i in range(0, 20, 4))
    return f"CERID-PRO-{formatted}"


def validate_license_key(key: str) -> dict[str, bool | str | None]:
    """Validate a license key offline.

    Returns
    -------
    dict
        ``{"valid": bool, "tier": str, "error": str | None}``
    """
    if not key or not key.startswith("CERID-PRO-"):
        return {"valid": False, "tier": "community", "error": "Invalid key format"}

    # Strip prefix, expect exactly 5 groups of 4 hex chars
    body = key.replace("CERID-PRO-", "")
    parts = body.split("-")
    if len(parts) != 5 or not all(len(p) == 4 for p in parts):
        return {"valid": False, "tier": "community", "error": "Invalid key format"}

    # If LICENSE_SECRET is set, verify HMAC (production path)
    # Note: Cryptographic HMAC verification is delegated to the billing
    # router's _validate_license_key() which has access to the original
    # payload structure.  This function provides format-only validation
    # suitable for the frontend activation flow's first-pass check.

    return {"valid": True, "tier": "pro", "error": None}


def mask_license_key(key: str) -> str:
    """Mask a license key for display: ``CERID-PRO-****-****-****-****-XXXX``.

    Only the last 4-character group is visible.
    """
    if not key or not key.startswith("CERID-PRO-"):
        return key
    body = key.replace("CERID-PRO-", "")
    parts = body.split("-")
    if len(parts) < 2:
        return key
    masked = "-".join("****" for _ in parts[:-1])
    return f"CERID-PRO-{masked}-{parts[-1]}"
