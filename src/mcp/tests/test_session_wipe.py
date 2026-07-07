# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 1.2b — L4 session-wipe orchestrator.

``wipe_conversation_state`` is the belt-and-suspenders cleanup for
whatever persisted for a conversation BEFORE it escalated to L4 (L1
already blocks new writes server-side). These tests cover: the three
reused deletion seams get called with the right ids, one store's
failure doesn't block the others, idempotent re-firing is a no-op, and
the HTTP endpoint's documented response shape survives even when Neo4j
is unreachable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.services.session_wipe as session_wipe
from app.routers.settings import _PRIVATE_MODE_KEY, _PRIVATE_MODE_SESSION_PREFIX, router
from app.services.session_wipe import wipe_conversation_state


class _FakeSession:
    """Records every Cypher call so tests can assert on query shape."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []

    def run(self, query, **params):
        self.queries.append((query, params))
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeDriver:
    def __init__(self) -> None:
        self.fake_session = _FakeSession()

    def session(self):
        return self.fake_session


# ---------------------------------------------------------------------------
# wipe_conversation_state — unit tests
# ---------------------------------------------------------------------------


def test_wipe_deletes_conversation_from_sync_dir_when_configured(monkeypatch):
    fake_delete = MagicMock()
    monkeypatch.setattr(session_wipe, "delete_conversation", fake_delete)
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: [])
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", lambda *_a: [])

    driver = _FakeDriver()
    summary = wipe_conversation_state("conv-1", sync_dir="/tmp/sync", neo4j_driver=driver)

    fake_delete.assert_called_once_with("/tmp/sync", "conv-1")
    assert summary["conversation_sync_deleted"] is True


def test_wipe_skips_sync_dir_when_not_configured(monkeypatch):
    fake_delete = MagicMock()
    monkeypatch.setattr(session_wipe, "delete_conversation", fake_delete)
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: [])
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", lambda *_a: [])

    summary = wipe_conversation_state("conv-1", sync_dir=None, neo4j_driver=_FakeDriver())

    fake_delete.assert_not_called()
    assert summary["conversation_sync_deleted"] is False


def test_wipe_deletes_each_memory_artifact_via_retention_helper(monkeypatch):
    """Two ids from the EXTRACTED_FROM lookup -> two calls to the reused
    both-stores purge helper (app.services.retention.apply_retention_plan)."""
    monkeypatch.setattr(session_wipe, "delete_conversation", MagicMock())
    monkeypatch.setattr(
        session_wipe, "_find_extracted_memory_artifact_ids",
        lambda *_a: ["art-1", "art-2"],
    )
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", lambda *_a: [])
    fake_purge = MagicMock()
    monkeypatch.setattr(session_wipe, "apply_retention_plan", fake_purge)

    driver = _FakeDriver()
    summary = wipe_conversation_state("conv-1", sync_dir=None, neo4j_driver=driver)

    assert fake_purge.call_count == 2
    purged_ids = [call.args[1].purge[0] for call in fake_purge.call_args_list]
    assert purged_ids == ["art-1", "art-2"]
    assert summary["memory_artifacts_deleted"] == 2
    assert summary["memory_artifacts_failed"] == 0

    # Conversation + VerificationReport node deletes also happened.
    queries = " ".join(q for q, _p in driver.fake_session.queries)
    assert "MATCH (c:Conversation {id: $cid}) DETACH DELETE c" in queries
    assert "VerificationReport {conversation_id: $cid}" in queries


def test_wipe_issues_conversation_and_verification_report_node_deletes(monkeypatch):
    monkeypatch.setattr(session_wipe, "delete_conversation", MagicMock())
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: [])
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", lambda *_a: [])

    driver = _FakeDriver()
    wipe_conversation_state("conv-1", sync_dir=None, neo4j_driver=driver)

    cids = [params.get("cid") for _q, params in driver.fake_session.queries]
    assert cids == ["conv-1", "conv-1"]


def test_wipe_skips_neo4j_steps_when_driver_is_none(monkeypatch):
    fake_lookup = MagicMock()
    fake_verified_lookup = MagicMock()
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", fake_lookup)
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", fake_verified_lookup)
    monkeypatch.setattr(session_wipe, "delete_conversation", MagicMock())

    summary = wipe_conversation_state("conv-1", sync_dir=None, neo4j_driver=None)

    fake_lookup.assert_not_called()
    fake_verified_lookup.assert_not_called()
    assert summary["conversation_node_deleted"] is False
    assert summary["verification_report_deleted"] is False


def test_wipe_is_best_effort_when_memory_delete_helper_raises(monkeypatch):
    """A failing memory-artifact delete must not block the conversation-node
    delete or the sync-store delete, and the failure must be logged via
    log_swallowed_error rather than propagating."""
    fake_delete_conversation = MagicMock()
    monkeypatch.setattr(session_wipe, "delete_conversation", fake_delete_conversation)
    monkeypatch.setattr(
        session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: ["art-1"],
    )
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", lambda *_a: [])
    monkeypatch.setattr(
        session_wipe, "apply_retention_plan",
        MagicMock(side_effect=RuntimeError("chroma unreachable")),
    )
    fake_log = MagicMock()
    monkeypatch.setattr(session_wipe, "log_swallowed_error", fake_log)

    driver = _FakeDriver()
    summary = wipe_conversation_state("conv-1", sync_dir="/tmp/sync", neo4j_driver=driver)

    fake_delete_conversation.assert_called_once_with("/tmp/sync", "conv-1")
    assert summary["memory_artifacts_failed"] == 1
    assert summary["memory_artifacts_deleted"] == 0
    # Node deletes still ran despite the artifact-delete failure.
    queries = " ".join(q for q, _p in driver.fake_session.queries)
    assert "DETACH DELETE c" in queries
    assert "VerificationReport" in queries
    assert any(
        call.args[0] == "session_wipe.memory_artifact_delete"
        for call in fake_log.call_args_list
    )


def test_wipe_is_idempotent_when_nothing_left_to_delete(monkeypatch):
    """Re-firing on an id with no artifacts/conversation left must not raise."""
    monkeypatch.setattr(session_wipe, "delete_conversation", MagicMock())
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: [])
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", lambda *_a: [])

    driver = _FakeDriver()
    first = wipe_conversation_state("conv-1", sync_dir="/tmp/sync", neo4j_driver=driver)
    second = wipe_conversation_state("conv-1", sync_dir="/tmp/sync", neo4j_driver=driver)

    assert first["memory_artifacts_failed"] == 0
    assert second["memory_artifacts_failed"] == 0
    assert second["conversation_node_deleted"] is True


# ---------------------------------------------------------------------------
# Verified-memory (:Memory / VERIFIED_BY) deletion — task 1.2b
# ---------------------------------------------------------------------------


def test_wipe_deletes_each_verified_memory_via_dedicated_helper(monkeypatch):
    """Two ids from the VERIFIED_BY lookup -> two calls to the dedicated
    verified-memory purge helper (_delete_verified_memory)."""
    monkeypatch.setattr(session_wipe, "delete_conversation", MagicMock())
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: [])
    monkeypatch.setattr(
        session_wipe, "_find_verified_memory_ids", lambda *_a: ["mem-1", "mem-2"],
    )
    fake_delete = MagicMock()
    monkeypatch.setattr(session_wipe, "_delete_verified_memory", fake_delete)

    driver = _FakeDriver()
    summary = wipe_conversation_state("conv-1", sync_dir=None, neo4j_driver=driver)

    assert fake_delete.call_count == 2
    deleted_ids = [call.args[1] for call in fake_delete.call_args_list]
    assert deleted_ids == ["mem-1", "mem-2"]
    assert summary["verified_memories_deleted"] == 2
    assert summary["verified_memories_failed"] == 0


def test_wipe_is_best_effort_when_verified_memory_delete_raises(monkeypatch):
    """A failing verified-memory delete must not block the
    VerificationReport delete, and the failure must be logged via
    log_swallowed_error rather than propagating."""
    monkeypatch.setattr(session_wipe, "delete_conversation", MagicMock())
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: [])
    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", lambda *_a: ["mem-1"])
    monkeypatch.setattr(
        session_wipe, "_delete_verified_memory",
        MagicMock(side_effect=RuntimeError("chroma unreachable")),
    )
    fake_log = MagicMock()
    monkeypatch.setattr(session_wipe, "log_swallowed_error", fake_log)

    driver = _FakeDriver()
    summary = wipe_conversation_state("conv-1", sync_dir=None, neo4j_driver=driver)

    assert summary["verified_memories_failed"] == 1
    assert summary["verified_memories_deleted"] == 0
    queries = " ".join(q for q, _p in driver.fake_session.queries)
    assert "VerificationReport" in queries
    assert any(
        call.args[0] == "session_wipe.verified_memory_delete"
        for call in fake_log.call_args_list
    )


def test_verified_memory_deletes_precede_verification_report_delete(monkeypatch):
    """The VERIFIED_BY correlator must not be destroyed before the linked
    :Memory nodes are found and deleted — reversing this order would make
    the linked memories permanently unfindable (the original defect)."""
    monkeypatch.setattr(session_wipe, "delete_conversation", MagicMock())
    monkeypatch.setattr(session_wipe, "_find_extracted_memory_artifact_ids", lambda *_a: [])

    call_order: list[str] = []

    def _fake_find(*_a):
        call_order.append("find_verified_memories")
        return ["mem-1"]

    monkeypatch.setattr(session_wipe, "_find_verified_memory_ids", _fake_find)
    monkeypatch.setattr(
        session_wipe, "_delete_verified_memory",
        lambda *_a: call_order.append("delete_verified_memory"),
    )
    monkeypatch.setattr(
        session_wipe, "_delete_verification_report_node",
        lambda *_a: call_order.append("delete_verification_report"),
    )

    driver = _FakeDriver()
    wipe_conversation_state("conv-1", sync_dir=None, neo4j_driver=driver)

    assert call_order == [
        "find_verified_memories",
        "delete_verified_memory",
        "delete_verification_report",
    ]


def test_delete_verified_memory_purges_chroma_and_neo4j(monkeypatch):
    """_delete_verified_memory deletes the deterministic Chroma companion
    doc (verified_memory_{id} in the "conversations" collection) and
    DETACH DELETEs the Neo4j :Memory node."""
    fake_collection = MagicMock()
    fake_chroma = MagicMock()
    fake_chroma.get_or_create_collection.return_value = fake_collection
    monkeypatch.setattr("app.deps.get_chroma", lambda: fake_chroma)

    driver = _FakeDriver()
    session_wipe._delete_verified_memory(driver, "mem-123")

    fake_collection.delete.assert_called_once_with(ids=["verified_memory_mem-123"])
    queries = driver.fake_session.queries
    assert any(
        "MATCH (m:Memory {id: $mid}) DETACH DELETE m" in q and p.get("mid") == "mem-123"
        for q, p in queries
    )


# ---------------------------------------------------------------------------
# Endpoint contract — POST /settings/private-mode/session-wipe
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, owner) -> None:
        self._owner = owner

    def delete(self, key):
        self._owner.store.pop(key, None)
        return self

    def execute(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)

    def pipeline(self):
        return _FakePipeline(self)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.deps.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.routers.settings.get_redis", lambda: fake_redis)
    return TestClient(app), fake_redis


def test_endpoint_returns_documented_shape_when_neo4j_unreachable(client, monkeypatch):
    """Neo4j being down must not break the wipe endpoint's contract — the
    get_neo4j() failure is swallowed and the store-backed steps are skipped."""
    tc, fake_redis = client
    monkeypatch.setattr(
        "app.routers.settings.get_neo4j",
        MagicMock(side_effect=RuntimeError("NEO4J_PASSWORD is empty")),
    )
    fake_redis.store[_PRIVATE_MODE_KEY] = "4"

    r = tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "conv-e2e"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "wiped": True,
        "level_after": 0,
        "conversation_id": "conv-e2e",
    }
    assert _PRIVATE_MODE_KEY not in fake_redis.store


def test_endpoint_clears_session_key_even_when_wipe_orchestrator_fails(client, monkeypatch):
    """A hard failure inside wipe_conversation_state itself (not just a
    missing driver) must not stop the Redis flag cleanup or the response."""
    tc, fake_redis = client
    monkeypatch.setattr("app.routers.settings.get_neo4j", lambda: object())
    monkeypatch.setattr(
        "app.routers.settings.wipe_conversation_state",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    session_key = f"{_PRIVATE_MODE_SESSION_PREFIX}conv-fail"
    fake_redis.store[session_key] = "4"

    r = tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "conv-fail"},
    )
    assert r.status_code == 200
    assert r.json()["wiped"] is True
    assert session_key not in fake_redis.store
