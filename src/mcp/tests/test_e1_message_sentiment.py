# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — message thumbs feedback lands in the chat feedback-loop store (CR-043).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-043). Message thumbs up/down POSTed to ``/artifacts/{message_id}/feedback`` —
an id-space mismatch (a chat-message uuid is not an artifact id) with the wrong
schema — so the feedback was structurally dark. It now records a per-message
sentiment into the conversation feedback-loop store. RED-then-GREEN.
"""
from __future__ import annotations

import json

import pytest


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}

    def hset(self, key, field, value):
        self.store.setdefault(key, {})[field] = value

    def expire(self, key, ttl):
        pass


def test_cr043_log_conversation_sentiment_writes_hash():
    from core.utils.cache import log_conversation_sentiment

    fake = _FakeRedis()
    log_conversation_sentiment(fake, "conv-1", "msg-1", "up")
    # Re-rating overwrites the same field (thumbs toggling doesn't flood).
    log_conversation_sentiment(fake, "conv-1", "msg-1", "down")

    assert fake.store["conv:conv-1:sentiment"] == {"msg-1": "down"}


@pytest.mark.asyncio
async def test_cr043_endpoint_records_sentiment_and_skips_ingest(monkeypatch):
    """A sentiment ping records to the feedback-loop store and acks 202 WITHOUT
    the heavy turn-ingest path. RED on HEAD: no sentiment field existed and the
    request fell through to the full-turn ingest enqueue."""
    from app.routers import ingestion
    from app.routers.ingestion import FeedbackIngestRequest

    fake = _FakeRedis()
    monkeypatch.setattr(ingestion, "get_redis", lambda: fake)
    monkeypatch.setattr(ingestion.config, "ENABLE_FEEDBACK_LOOP", True)

    resp = await ingestion.ingest_feedback_endpoint(
        FeedbackIngestRequest(conversation_id="conv-9", message_id="msg-9", sentiment="down")
    )

    assert resp.status_code == 202
    assert json.loads(resp.body)["status"] == "sentiment_recorded"
    assert fake.store["conv:conv-9:sentiment"]["msg-9"] == "down"
