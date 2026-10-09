# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-5 — deleting a conversation clears its hall:{cid} report (CR-012).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-012). Neither the plain conversation-delete (DELETE /conversations/{id}) nor
the L4 session-wipe orchestrator cleared the durable Redis ``hall:{cid}``
verification report — verbatim claims + source snippets — so it survived
deletion for its 7-day TTL. RED-then-GREEN.
"""
from __future__ import annotations

from unittest.mock import MagicMock


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def delete(self, *keys):
        return sum(1 for key in keys if self.store.pop(key, None) is not None)

    def zrem(self, name, *members):
        return 0


def test_delete_hallucination_report_removes_key():
    from core.agents.hallucination import (
        REDIS_HALLUCINATION_PREFIX,
        delete_hallucination_report,
    )

    fake = _FakeRedis()
    key = f"{REDIS_HALLUCINATION_PREFIX}cid-1"
    fake.store[key] = "{}"

    assert delete_hallucination_report(fake, "cid-1") is True
    assert key not in fake.store
    # Idempotent — a miss returns False, never raises.
    assert delete_hallucination_report(fake, "cid-1") is False


def test_permanent_conversation_delete_clears_hall_cache(monkeypatch, tmp_path):
    """A plain DELETE now moves the conversation to Trash (restorable), so the
    report goes when the forget is purged: here, ``?permanent=true``."""
    import app.routers.user_state as us
    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX
    from tests.helpers.forget import isolate_forget

    fake = _FakeRedis()
    key = f"{REDIS_HALLUCINATION_PREFIX}cid-3"
    fake.store[key] = '{"claims": ["secret"]}'

    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr(us, "_sync_dir", lambda: str(tmp_path))
    monkeypatch.setattr("app.services.content_lifecycle.remove_conversation_transcripts", lambda cid: [])
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)
    monkeypatch.setattr("app.deps.get_neo4j", lambda: MagicMock())

    result = us.remove_conversation("cid-3", permanent=True)

    assert result["deleted"] == "cid-3" and result["state"] == "purged"
    assert key not in fake.store


def test_trashed_conversation_hall_report_is_unreachable_until_restored(monkeypatch, tmp_path):
    """A plain DELETE keeps hall:{cid} for a possible restore, but no read
    returns it while the conversation is in the trash (CR-012 for the default
    delete)."""
    import fakeredis

    import app.routers.user_state as us
    from app.services.forget import engine
    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX, get_hallucination_report
    from tests.helpers.forget import isolate_forget

    redis = fakeredis.FakeRedis(decode_responses=True)
    redis.set(f"{REDIS_HALLUCINATION_PREFIX}cid-9", '{"claims": ["secret claim"]}')
    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr(us, "_sync_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "app.services.content_lifecycle.conversation_transcript_artifact_ids", lambda cid: [],
    )

    result = us.remove_conversation("cid-9")

    assert result["state"] == "trashed"
    assert get_hallucination_report(redis, "cid-9") is None
    engine.restore(result["forget_id"])
    assert get_hallucination_report(redis, "cid-9") == {"claims": ["secret claim"]}
