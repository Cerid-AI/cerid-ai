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
    JSON string. The previous Cypher-CONTAINS form was fragile
    against ``json.dumps`` whitespace conventions.
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


def test_find_webhook_source_resolves_all_webhook_family_kinds():
    """Tokens minted for chat_capture / dev_events must resolve too.

    Regression: the receiver only listed kind="webhook", so chat_capture
    and dev_events sources (also KIND_FAMILY == "webhook") were minted a
    token at create time but 404'd on every inbound POST — dead on arrival.
    """
    by_kind = {
        "webhook": [{"id": "w", "kind": "webhook", "config": {"token": "tok-w"}}],
        "chat_capture": [
            {"id": "c", "kind": "chat_capture", "config": {"token": "tok-c"}}
        ],
        "dev_events": [
            {"id": "d", "kind": "dev_events", "config": {"token": "tok-d"}}
        ],
    }

    def _list(driver, *, kind=None):
        return by_kind.get(kind, [])

    with patch.object(webhook_tokens.srcdb, "list_sources", side_effect=_list):
        for token, expected_id in [("tok-c", "c"), ("tok-d", "d"), ("tok-w", "w")]:
            match = webhook_tokens.find_webhook_source(None, token)
            assert match is not None
            assert match["id"] == expected_id
        assert webhook_tokens.find_webhook_source(None, "nope") is None

    # The lookup must cover exactly the webhook-family kinds, derived from
    # the same family map the create path uses (no hardcoded kind list).
    from core.ingest.sources.kinds import KIND_FAMILY

    expected = {k for k, fam in KIND_FAMILY.items() if fam == "webhook"}
    assert set(webhook_tokens._WEBHOOK_FAMILY_KINDS) == expected


def test_verify_hmac_signature_accepts_prefixed_and_bare():
    secret = "test-secret"  # pragma: allowlist secret
    body = b'{"hello":"world"}'
    import hmac

    digest = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()

    assert webhook_tokens.verify_hmac_signature(secret, body, f"sha256={digest}")
    assert webhook_tokens.verify_hmac_signature(secret, body, digest)
    assert not webhook_tokens.verify_hmac_signature(secret, body, "deadbeef")
    assert not webhook_tokens.verify_hmac_signature("", body, digest)
    assert not webhook_tokens.verify_hmac_signature(secret, body, "")
