# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""License key validation for Cerid Pro tier.

Format: ``CERID-PRO-`` followed by base32 groups of 4 whose decoded body is the
signed payload + signature. Key generation and full cryptographic verification
are handled by the commercial build / billing service; this open-source edition
performs a format-only check and accepts any correctly-formed key.
"""
from __future__ import annotations

import base64


def validate_license_key(key: str) -> dict[str, bool | str | None]:
    """Validate a license key format (offline check).

    Format-only: confirms a well-formed ``CERID-PRO-`` token whose body
    base32-decodes to the expected 72-byte payload+signature. The signature
    itself is verified by the commercial build.

    Returns
    -------
    dict
        ``{"valid": bool, "tier": str, "error": str | None}``
    """
    invalid: dict[str, bool | str | None] = {
        "valid": False, "tier": "community", "error": "Invalid key format",
    }
    if not key or not key.startswith("CERID-PRO-"):
        return invalid

    body = key[len("CERID-PRO-"):].replace("-", "")
    try:
        raw = base64.b32decode(body.upper() + "=" * (-len(body) % 8))
    except (ValueError, TypeError):
        return invalid
    if len(raw) != 72:  # 8-byte payload + 64-byte Ed25519 signature
        return invalid

    return {"valid": True, "tier": "pro", "error": None}


def mask_license_key(key: str) -> str:
    """Mask a license key for display: ``CERID-PRO-****-****-****-****-XXXX``."""
    if not key or not key.startswith("CERID-PRO-"):
        return key
    body = key.replace("CERID-PRO-", "")
    parts = body.split("-")
    if len(parts) < 2:
        return key
    masked = "-".join("****" for _ in parts[:-1])
    return f"CERID-PRO-{masked}-{parts[-1]}"
