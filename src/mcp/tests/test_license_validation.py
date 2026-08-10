# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Offline license-key verification — the real-vs-forged distinction.

This file runs in BOTH editions and deliberately signs keys inline rather than
calling the issuer helper, so it pins the *wire format* both sides must agree
on rather than one implementation's behaviour. Until v1.0.2 the community
validator checked only a key's shape, so any well-formed 72-byte blob unlocked
Pro; these tests exist so that cannot silently return.
"""

from __future__ import annotations

import base64
import importlib
import struct
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_VERSION = 0x02
_SECONDS_PER_DAY = 86400


def _b32(data: bytes) -> str:
    return base64.b32encode(data).decode().rstrip("=")


def _mint(priv: Ed25519PrivateKey, *, tier_byte: int = 0x01,
          days_valid: int = 365, version: int = _VERSION) -> str:
    """Sign a key exactly the way the issuer does (see marketing lib/license.ts)."""
    expiry_day = 0 if days_valid == 0 else int(time.time()) // _SECONDS_PER_DAY + days_valid
    payload = struct.pack(">HBBI", expiry_day, tier_byte, version, 0xDEADBEEF)
    body = _b32(payload + priv.sign(payload))
    return "CERID-PRO-" + "-".join(body[i:i + 4] for i in range(0, len(body), 4))


def _pub_b64(priv: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


@pytest.fixture
def issuer(monkeypatch):
    """A throwaway issuer whose verify key the module is reloaded against.

    The verify key is captured at import time, so the reload is required —
    without it the module keeps the shipped production key and every minted
    test key is (correctly) rejected.
    """
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setenv("CERID_LICENSE_PUBLIC_KEY", _pub_b64(priv))
    import utils.license as lic

    importlib.reload(lic)
    yield priv, lic
    monkeypatch.delenv("CERID_LICENSE_PUBLIC_KEY", raising=False)
    importlib.reload(lic)


def test_a_genuinely_signed_key_is_accepted(issuer):
    priv, lic = issuer
    result = lic.validate_license_key(_mint(priv))
    assert result["valid"] is True
    assert result["tier"] == "pro"
    assert result["expires_at"] is not None


def test_the_tier_byte_is_honoured(issuer):
    priv, lic = issuer
    assert lic.validate_license_key(_mint(priv, tier_byte=0x02))["tier"] == "enterprise"


def test_a_perpetual_key_reports_no_expiry(issuer):
    priv, lic = issuer
    result = lic.validate_license_key(_mint(priv, days_valid=0))
    assert result["valid"] is True
    assert result["expires_at"] is None


def test_a_well_formed_but_unsigned_blob_is_rejected(issuer):
    """The whole point: correct shape is not enough."""
    _priv, lic = issuer
    body = _b32(b"\x01" * 72)
    forged = "CERID-PRO-" + "-".join(body[i:i + 4] for i in range(0, len(body), 4))
    assert lic.validate_license_key(forged)["valid"] is False


def test_a_key_from_a_different_issuer_is_rejected(issuer):
    _priv, lic = issuer
    assert lic.validate_license_key(_mint(Ed25519PrivateKey.generate()))["valid"] is False


@pytest.mark.parametrize("byte_index", [0, 4, 8, 40, 71])
def test_flipping_any_body_byte_invalidates_the_key(issuer, byte_index):
    """Covers payload bytes (0-7) and signature bytes (8-71) alike.

    Mutates BYTES, not base32 characters: the final character of a key carries
    only one significant bit, so editing it can decode to the same body and
    look like a tamper that "passed".
    """
    priv, lic = issuer
    key = _mint(priv)
    chars = key[len("CERID-PRO-"):].replace("-", "")
    raw = base64.b32decode(chars + "=" * (-len(chars) % 8))
    flipped = raw[:byte_index] + bytes([raw[byte_index] ^ 0x01]) + raw[byte_index + 1:]
    body = _b32(flipped)
    mutated = "CERID-PRO-" + "-".join(body[i:i + 4] for i in range(0, len(body), 4))
    assert lic.validate_license_key(mutated)["valid"] is False


def test_an_expired_key_is_rejected_but_readable_with_the_override(issuer):
    priv, lic = issuer
    # days_valid is added to today, so a negative value back-dates the expiry.
    stale = _mint(priv, days_valid=-2)
    assert lic.validate_license_key(stale)["valid"] is False
    assert lic.validate_license_key(stale, check_expiry=False)["valid"] is True


def test_a_retired_scheme_version_is_rejected(issuer):
    priv, lic = issuer
    assert lic.validate_license_key(_mint(priv, version=0x01))["valid"] is False


def test_an_unknown_tier_byte_is_rejected(issuer):
    priv, lic = issuer
    assert lic.validate_license_key(_mint(priv, tier_byte=0x7F))["valid"] is False


@pytest.mark.parametrize("bad", ["", "nope", "CERID-PRO-", "CERID-PRO-!!!!", "CERID-PRO-AAAA"])
def test_malformed_input_is_rejected(issuer, bad):
    _priv, lic = issuer
    assert lic.validate_license_key(bad)["valid"] is False


def test_verification_can_be_read_off_the_module(issuer):
    _priv, lic = issuer
    assert lic.verification_enabled() is True


def test_an_empty_verify_key_disables_verification(monkeypatch):
    """Preview mode is allowed, but it must be introspectable — the server
    warns at boot on the strength of this flag."""
    monkeypatch.setenv("CERID_LICENSE_PUBLIC_KEY", "")
    import utils.license as lic

    importlib.reload(lic)
    try:
        assert lic.verification_enabled() is False
        body = _b32(b"\x02" * 72)
        anything = "CERID-PRO-" + "-".join(body[i:i + 4] for i in range(0, len(body), 4))
        assert lic.validate_license_key(anything)["valid"] is True
    finally:
        monkeypatch.delenv("CERID_LICENSE_PUBLIC_KEY", raising=False)
        importlib.reload(lic)


# --- Regressions from the 2026-08-10 entitlement audit -----------------------


class TestMalformedVerifyKeyFailsClosed:
    """A verify key that is SET but unparseable must reject everything.

    Both "unset" and "malformed" used to return None from _load_public_key,
    and None means dev mode: accept any format-valid key as Pro. So a
    truncated or whitespace-mangled CERID_LICENSE_PUBLIC_KEY — an ordinary
    env-file copy-paste slip — silently turned verification OFF and minted
    perpetual Pro for any correctly-shaped blob. Misconfiguration must fail
    closed, never open.
    """

    def _reload(self, monkeypatch, value: str):
        import importlib

        import utils.license as lic

        monkeypatch.setenv("CERID_LICENSE_PUBLIC_KEY", value)
        return importlib.reload(lic)

    def test_a_malformed_key_is_detected(self, monkeypatch):
        lic = self._reload(monkeypatch, "this-is-not-base64-of-an-ed25519-key!!")
        assert lic.public_key_is_malformed() is True

    def test_a_malformed_key_rejects_a_well_shaped_licence(self, monkeypatch):
        lic = self._reload(monkeypatch, "short")
        shaped = "CERID-PRO-" + "-".join(["AAAA"] * 29)
        out = lic.validate_license_key(shaped)
        assert out["valid"] is False
        assert out["tier"] == "community"

    def test_an_unset_key_is_not_reported_malformed(self, monkeypatch):
        """Dev mode is a deliberate configuration, not an error — keep the
        two distinguishable so the boot log can name the right one."""
        lic = self._reload(monkeypatch, "")
        assert lic.public_key_is_malformed() is False


class TestMaskLicenseKey:
    """`mask_license_key` had NO tests in either tree, which is how two
    defects survived in it: a fail-open that echoed an unrecognised key back
    verbatim, and (public only) a per-group mask that rendered ~150 characters
    of asterisks into the settings field for a real 29-group key. Both were
    found by activating a key against a running server, not by reading."""

    def _real_shaped_key(self) -> str:
        # 29 groups, the shape validate_license_key actually accepts.
        return "CERID-PRO-" + "-".join(["ABCD"] * 28 + ["VQCA"])

    def test_collapses_the_body_to_a_single_run(self):
        from utils.license import mask_license_key

        masked = mask_license_key(self._real_shaped_key())
        assert masked == "CERID-PRO-****-VQCA"
        # The defect this pins: one run, not one per group.
        assert masked.count("****") == 1

    def test_stays_short_for_a_real_length_key(self):
        from utils.license import mask_license_key

        assert len(mask_license_key(self._real_shaped_key())) < 32

    def test_reveals_the_last_group_so_two_keys_are_distinguishable(self):
        from utils.license import mask_license_key

        a = mask_license_key("CERID-PRO-" + "-".join(["ABCD"] * 28 + ["AAAA"]))
        b = mask_license_key("CERID-PRO-" + "-".join(["ABCD"] * 28 + ["BBBB"]))
        assert a != b

    def test_an_ungrouped_body_is_not_echoed_back(self):
        """Fails CLOSED. This value goes into an API response, so returning the
        input verbatim on an unexpected shape would leak the key."""
        from utils.license import mask_license_key

        raw = "CERID-PRO-" + "Z" * 60
        masked = mask_license_key(raw)
        assert raw not in masked
        assert masked.startswith("CERID-PRO-****")

    def test_a_foreign_string_is_returned_unchanged(self):
        """Not a key at all — nothing to mask, and mangling it would hide a
        config error from the operator."""
        from utils.license import mask_license_key

        assert mask_license_key("not-a-key") == "not-a-key"
        assert mask_license_key("") == ""
