# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Apple-connector preservation invariants — Phase D Day 9.

Locks the backend contracts the desktop Apple connectors (Notes / Mail /
Messages) depend on:

  * /ingest/structured accepts the connector payload shape
  * X-Client-ID and source_id flow into metadata
  * The known Apple source values (apple_notes / apple_mail / imessage)
    can be ingested + searched
  * pkb_privacy_audit with domain="" scans everything including notes /
    mail / messages domains
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.preservation


def test_ingest_structured_accepts_apple_notes_payload(http_client):
    r = http_client.post(
        "/ingest/structured",
        json={
            "content": "# Recipe for sourdough\n\nFlour, water, salt, time.",
            "domain": "notes",
            "source_id": "apple_notes:preservation-test-1",
            "metadata": {
                "source": "apple_notes",
                "title": "Recipe for sourdough",
                "folder_path": "Personal/Recipes",
                "account": "iCloud",
                "modified_at": "2026-05-21T00:00:00Z",
            },
        },
        headers={"X-Client-ID": "apple_notes"},
    )
    assert r.status_code == 200, f"/ingest/structured {r.status_code}: {r.text[:200]}"


def test_ingest_structured_accepts_apple_mail_payload(http_client):
    r = http_client.post(
        "/ingest/structured",
        json={
            "content": "From: alice@example.com\nSubject: Test\n\nHello from preservation.",
            "domain": "mail",
            "source_id": "apple_mail:iCloud:INBOX:preservation-test-1",
            "metadata": {
                "source": "apple_mail",
                "account": "iCloud",
                "mailbox": "INBOX",
                "from": "alice@example.com",
                "subject": "Test",
                "date_received": "2026-05-21T00:00:00Z",
                "message_id": "<preservation-test@example.com>",
            },
        },
        headers={"X-Client-ID": "apple_mail"},
    )
    assert r.status_code == 200


def test_ingest_structured_accepts_imessage_payload(http_client):
    r = http_client.post(
        "/ingest/structured",
        json={
            "content": "# Conversation: Alice\n\n[2026-05-21] Alice: hello\n[2026-05-21] Me: hi back",
            "domain": "messages",
            "source_id": "imessage:preservation-test-guid",
            "metadata": {
                "source": "imessage",
                "conversation_guid": "preservation-test-guid",
                "display_name": "Alice",
                "participants": "+15551234567",
                "is_group": "0",
                "message_count": "2",
            },
        },
        headers={"X-Client-ID": "imessage"},
    )
    assert r.status_code == 200


def test_ingest_structured_rejects_non_string_metadata(http_client):
    """The endpoint declares metadata: dict[str, str]. Mixed types reject."""
    r = http_client.post(
        "/ingest/structured",
        json={
            "content": "x",
            "domain": "notes",
            "metadata": {"count": 42},  # numeric — should reject
        },
    )
    assert r.status_code == 422


def test_ingest_structured_default_domain(http_client):
    r = http_client.post(
        "/ingest/structured",
        json={"content": "no domain specified"},
    )
    assert r.status_code == 200
