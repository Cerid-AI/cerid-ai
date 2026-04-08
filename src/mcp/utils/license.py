# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""License key validation for Cerid Pro tier.

Format: CERID-PRO-XXXX-XXXX-XXXX-XXXX-XXXX (5 groups of 4 characters).
Key generation is handled server-side by the billing service.
"""
from __future__ import annotations


def validate_license_key(key: str) -> dict[str, bool | str | None]:
    """Validate a license key format (offline check)."""
    if not key or not key.startswith("CERID-PRO-"):
        return {"valid": False, "tier": "community", "error": "Invalid key format"}
    body = key.replace("CERID-PRO-", "")
    parts = body.split("-")
    if len(parts) != 5 or not all(len(p) == 4 for p in parts):
        return {"valid": False, "tier": "community", "error": "Invalid key format"}
    return {"valid": True, "tier": "pro", "error": None}


def mask_license_key(key: str) -> str:
    """Mask a license key for display: CERID-PRO-****-****-****-****-XXXX."""
    if not key or not key.startswith("CERID-PRO-"):
        return key
    body = key.replace("CERID-PRO-", "")
    parts = body.split("-")
    if len(parts) < 2:
        return key
    masked = "-".join("****" for _ in parts[:-1])
    return f"CERID-PRO-{masked}-{parts[-1]}"
