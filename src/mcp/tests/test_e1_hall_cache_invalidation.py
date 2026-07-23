# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — deleting a conversation clears its hall:{cid} report (CR-012).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-012). Neither the plain conversation-delete (DELETE /conversations/{id}) nor
the L4 session-wipe orchestrator cleared the durable Redis ``hall:{cid}``
verification report — verbatim claims + source snippets — so it survived
deletion for its 7-day TTL. RED-then-GREEN.
"""
from __future__ import annotations


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0


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


def test_session_wipe_clears_hall_cache():
    from app.services.session_wipe import wipe_conversation_state
    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX

    fake = _FakeRedis()
    key = f"{REDIS_HALLUCINATION_PREFIX}cid-2"
    fake.store[key] = '{"claims": ["secret"]}'

    summary = wipe_conversation_state(
        "cid-2", sync_dir=None, neo4j_driver=None, redis_client=fake
    )

    assert summary["hallucination_cache_deleted"] is True
    assert key not in fake.store


def test_conversation_delete_clears_hall_cache(monkeypatch):
    import app.routers.user_state as us
    from core.agents.hallucination import REDIS_HALLUCINATION_PREFIX

    fake = _FakeRedis()
    key = f"{REDIS_HALLUCINATION_PREFIX}cid-3"
    fake.store[key] = '{"claims": ["secret"]}'

    monkeypatch.setattr(us, "_sync_dir", lambda: "/tmp/sync")
    monkeypatch.setattr(us, "delete_conversation", lambda sd, cid: None)
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)

    result = us.remove_conversation("cid-3")

    assert result == {"deleted": "cid-3"}
    assert key not in fake.store
