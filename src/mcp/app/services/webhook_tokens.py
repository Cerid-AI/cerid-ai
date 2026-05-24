# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Webhook-token service — generates, validates, and looks up the
signed tokens that gate the ``POST /sdk/v1/ingest/webhook/{token}``
endpoint.

A webhook-kind Source's ``config`` payload carries the canonical
token + the optional HMAC secret. The token IS the routing key
(used in the URL path) so we can resolve the Source in a single
indexed lookup without bouncing through Redis.

Token format: 32 URL-safe random characters (~190 bits of entropy
from ``secrets.token_urlsafe(24)``). Stored verbatim on the Source
node; the URL contains the same string. Constant-time compare on
the validation path so timing analysis can't enumerate tokens.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from typing import Any

from app.db.neo4j import sources as srcdb

logger = logging.getLogger("ai-companion.services.webhook_tokens")


def generate_token() -> str:
    """Return a fresh URL-safe webhook token (~190 bits of entropy).

    Length is 32 chars after ``token_urlsafe(24)`` — fits in a
    URL path segment without encoding and is short enough to display
    in the F7 share card without wrapping.
    """
    return secrets.token_urlsafe(24)


def generate_hmac_secret() -> str:
    """Return a fresh HMAC secret for X-Cerid-Signature verification.

    Operators can rotate by calling this and updating the Source
    config — the previous secret stops working immediately.
    """
    return secrets.token_urlsafe(32)


def find_webhook_source(driver, token: str) -> dict[str, Any] | None:
    """Look up the (:Source {kind: 'webhook'}) record whose config
    carries this token. Returns the source record dict (with config
    deserialized) or None if no match.

    Implementation: list all webhook-kind sources (typically <20)
    and filter Python-side on the deserialized config. Cypher string
    matching against the JSON blob is fragile because Python's
    ``json.dumps`` adds whitespace inside object literals which
    won't appear in a hand-built needle. A v1.1 optimization could
    break the token out into a separate indexed property if the
    webhook-source count grows substantially.
    """
    candidates = srcdb.list_sources(driver, kind="webhook")
    for src in candidates:
        config = src.get("config") or {}
        if isinstance(config, dict) and config.get("token") == token:
            return src
    return None


def verify_hmac_signature(
    secret: str,
    body: bytes,
    signature_header: str,
) -> bool:
    """Constant-time verification of ``X-Cerid-Signature: sha256=<hex>``.

    Accepts both the ``sha256=`` prefixed form (mirrors GitHub's
    convention) and a bare hex digest. Returns True iff the
    computed HMAC matches. False on any parse error — never raises.
    """
    if not secret or not signature_header:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    sig = signature_header.strip()
    if sig.startswith("sha256="):
        sig = sig[7:]
    return hmac.compare_digest(expected, sig)
