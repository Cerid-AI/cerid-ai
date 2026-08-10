# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""License key validation for Cerid Pro tier (community edition).

Keys are **offline-verifiable** with an **asymmetric (Ed25519)** scheme. Cerid
signs a key with a private signing key held only on the billing host; this
build verifies it locally with the **public** key embedded below. No network
round-trip, no license server, and no way to mint keys from this source — the
public key can only verify, never sign, so publishing it costs nothing.

Format: ``CERID-PRO-`` + base32 groups of 4 (RFC 4648, uppercase, no padding)
of a 72-byte body:

- ``body[:8]`` — signed payload, ``struct.pack(">HBBI", ...)``: ``expiry_day``
  (uint16, days since epoch; ``0`` ⇒ perpetual), ``tier_byte`` (uint8),
  ``version`` (uint8, ``0x02`` = Ed25519), ``email_fp`` (uint32 — audit only).
- ``body[8:]`` — the 64-byte Ed25519 signature over those 8 payload bytes.

Until v1.0.2 this module checked only the *shape* of a key, so any well-formed
72-byte blob unlocked Pro. That predated the Ed25519 scheme; there is no reason
for the open-core build to be unable to tell a real key from a fabricated one.
"""
from __future__ import annotations

import base64
import os
import struct
import time
from typing import TypedDict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Public VERIFY key — safe to ship: it cannot produce signatures. Override via
# env for key rotation. Setting it empty disables verification (dev/preview
# only) and the server logs that it has done so.
_DEFAULT_PUBLIC_KEY_B64 = "QDcetCt0wioUsh2Pfl0v79/U8IMbXCxkpGr8xeaI1w4="  # pragma: allowlist secret  (public verify key — safe to ship)
_PUBLIC_KEY_B64 = os.getenv("CERID_LICENSE_PUBLIC_KEY", _DEFAULT_PUBLIC_KEY_B64)  # env-capture-allowed: Ed25519 verify key — startup-only

_VERSION = 0x02  # 0x02 = Ed25519 (0x01 was the retired HMAC scheme)
_BYTE_TIERS = {0x01: "pro", 0x02: "enterprise"}
_SECONDS_PER_DAY = 86400
_PAYLOAD_LEN = 8
_SIG_LEN = 64
_BODY_LEN = _PAYLOAD_LEN + _SIG_LEN  # 72 bytes


class LicenseValidation(TypedDict):
    valid: bool
    tier: str | None
    expires_at: int | None
    error: str | None


def public_key_is_malformed() -> bool:
    """True when a verify key IS configured but cannot be parsed.

    Distinguishing this from "unset" is load-bearing. Both used to return
    ``None`` from :func:`_load_public_key`, and ``None`` means dev mode —
    accept any format-valid key as Pro. So a truncated or whitespace-mangled
    ``CERID_LICENSE_PUBLIC_KEY`` (an ordinary env-file copy-paste slip, and
    ``base64.b64decode`` is lenient enough to let plenty through) silently
    turned verification OFF and minted perpetual Pro for any 72-byte blob.
    A misconfigured verifier must reject everything, never accept everything.
    """
    if not _PUBLIC_KEY_B64:
        return False
    return _parse_public_key() is None


def _parse_public_key() -> Ed25519PublicKey | None:
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(_PUBLIC_KEY_B64))
    except (ValueError, TypeError):
        return None


def _load_public_key() -> Ed25519PublicKey | None:
    """The configured verify key, or ``None`` when genuinely unset (dev mode).

    Callers MUST check :func:`public_key_is_malformed` first — see its
    docstring for why ``None`` alone is not safe to act on.
    """
    if not _PUBLIC_KEY_B64:
        return None
    return _parse_public_key()


def verification_enabled() -> bool:
    """False when no verify key is configured, i.e. any shaped key is accepted.

    Exposed so the server can say so out loud at boot rather than silently
    accepting fabricated keys.
    """
    return _load_public_key() is not None


def validate_license_key(key: str, *, check_expiry: bool = True) -> LicenseValidation:
    """Validate a license key offline (signature + version + tier + expiry).

    Returns ``{"valid", "tier", "expires_at", "error"}``. With no verify key
    configured this degrades to a format-only check — see
    :func:`verification_enabled`.
    """
    fmt_error: LicenseValidation = {
        "valid": False, "tier": "community", "expires_at": None,
        "error": "Invalid license key format.",
    }
    if not key or not key.startswith("CERID-PRO-"):
        return fmt_error

    body = key[len("CERID-PRO-"):].replace("-", "")
    try:
        raw = base64.b32decode(body.upper() + "=" * (-len(body) % 8))
    except (ValueError, TypeError):
        return fmt_error
    if len(raw) != _BODY_LEN:
        return fmt_error
    payload, signature = raw[:_PAYLOAD_LEN], raw[_PAYLOAD_LEN:]

    if public_key_is_malformed():
        # Configured but unparseable: fail CLOSED. Falling through to the
        # dev-mode branch below would accept every shaped key as Pro.
        return {
            "valid": False, "tier": "community", "expires_at": None,
            "error": "License verification is misconfigured; contact your administrator.",
        }

    pub = _load_public_key()
    if pub is None:
        # Dev/preview: no verify key → accept format-valid keys, no semantics.
        return {"valid": True, "tier": "pro", "expires_at": None, "error": None}

    reject: LicenseValidation = {
        "valid": False, "tier": "community", "expires_at": None,
        "error": "Invalid or expired license key.",
    }
    try:
        pub.verify(signature, payload)
    except InvalidSignature:
        return reject

    expiry_day, tier_byte, version, _email_fp = struct.unpack(">HBBI", payload)
    if version != _VERSION:
        return reject
    tier = _BYTE_TIERS.get(tier_byte)
    if tier is None:
        return reject
    expires_at = expiry_day * _SECONDS_PER_DAY if expiry_day else None
    if check_expiry and expires_at is not None and int(time.time()) > expires_at:
        return {
            "valid": False, "tier": "community", "expires_at": expires_at,
            "error": "Invalid or expired license key.",
        }
    return {"valid": True, "tier": tier, "expires_at": expires_at, "error": None}


# Trailing characters left visible when masking an ungrouped key body.
_MASK_REVEAL_CHARS = 4


def mask_license_key(key: str) -> str:
    """Mask a license key for display: reveal only the last group.

    Format-agnostic — works for any number of body groups. The long Ed25519
    body collapses to a single ``****`` run so the UI shows
    ``CERID-PRO-****-<last>``.

    This used to mask each group individually, which matched the four-group
    example the old docstring gave but not reality: a real key is 29 groups, so
    an activated open-core install rendered ~150 characters of asterisks into
    the Plan & Billing field. The internal tree already collapsed it; this file
    is a sanctioned fork and never received the port. Found 2026-08-10 by
    activating a key against the running sandbox — no test covered this
    function in either tree.
    """
    if not key or not key.startswith("CERID-PRO-"):
        return key
    body = key.replace("CERID-PRO-", "")
    parts = body.split("-")
    if len(parts) < 2:
        # An ungrouped body is malformed, but this value goes into an API
        # response — echoing the key back would leak it. Fail closed.
        return (
            f"CERID-PRO-****-{body[-_MASK_REVEAL_CHARS:]}"
            if len(body) > _MASK_REVEAL_CHARS
            else "CERID-PRO-****"
        )
    return f"CERID-PRO-****-{parts[-1]}"
