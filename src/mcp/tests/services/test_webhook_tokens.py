# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for app.services.webhook_tokens.

The find_webhook_source matcher previously used a fragile Cypher
``CONTAINS`` against a hand-built ``"token":"X"`` substring; that
broke because ``json.dumps`` emits ``"token": "X"`` with whitespace
inside object literals. This test pins the new list+filter form so
the regression cannot recur silently.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services import webhook_tokens


def test_generate_token_is_url_safe_and_long_enough():
    token = webhook_tokens.generate_token()
    assert len(token) >= 30  # token_urlsafe(24) → 32 chars typically
    # URL-safe alphabet: A-Z a-z 0-9 - _
    assert all(c.isalnum() or c in "-_" for c in token)


def test_generate_hmac_secret_distinct_from_token():
    a = webhook_tokens.generate_token()
    b = webhook_tokens.generate_hmac_secret()
    assert a != b
    assert len(b) >= 40


def test_find_webhook_source_matches_against_dict_config():
    """Confirms the matcher works against the actual stored shape —
    a dict (already deserialized by srcdb._node_to_dict), not a raw
    JSON string. Regression guard against the 2026-05-24 fix.
    """
    fake_sources = [
        {"id": "a", "kind": "webhook", "config": {"token": "wanted-token"}},
        {"id": "b", "kind": "webhook", "config": {"token": "other-token"}},
    ]
    with patch.object(
        webhook_tokens.srcdb,
        "list_sources",
        return_value=fake_sources,
    ):
        match = webhook_tokens.find_webhook_source(driver=None, token="wanted-token")
        assert match is not None
        assert match["id"] == "a"

        miss = webhook_tokens.find_webhook_source(driver=None, token="no-match")
        assert miss is None


def test_find_webhook_source_tolerates_empty_or_missing_config():
    fake_sources = [
        {"id": "a", "kind": "webhook", "config": None},
        {"id": "b", "kind": "webhook"},  # config key absent
        {"id": "c", "kind": "webhook", "config": {"token": "real"}},
    ]
    with patch.object(
        webhook_tokens.srcdb,
        "list_sources",
        return_value=fake_sources,
    ):
        match = webhook_tokens.find_webhook_source(driver=None, token="real")
        assert match is not None
        assert match["id"] == "c"


def test_verify_hmac_signature_accepts_prefixed_and_bare():
    secret = "test-secret"
    body = b'{"hello":"world"}'
    import hmac

    digest = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()

    assert webhook_tokens.verify_hmac_signature(secret, body, f"sha256={digest}")
    assert webhook_tokens.verify_hmac_signature(secret, body, digest)
    assert not webhook_tokens.verify_hmac_signature(secret, body, "deadbeef")
    assert not webhook_tokens.verify_hmac_signature("", body, digest)
    assert not webhook_tokens.verify_hmac_signature(secret, body, "")
