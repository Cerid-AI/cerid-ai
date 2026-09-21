# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""L4 backend enforcement (v0.93.5).

The UI has rendered L4 ("Full ephemeral") since v0.92.1 but the backend
validator was capped at ``le=3``, so an API client posting level=4 got
a 422 and the lifecycle contract was half-shipped.  These tests verify
the three deliverables of the v0.93.5 L4 enforcement pass:

1. ``PrivateModeRequest`` accepts levels 0–4 and rejects 5+ / -1.
2. ``POST /settings/private-mode/session-wipe`` clears the per-session
   override, winds the global flag back to an explicit ``"0"`` once no
   other L4 session is registered (F021 — it used to DELETE the shared
   key, dropping every other tab), and returns a stable confirmation
   shape.
3. The wipe endpoint is idempotent — re-firing on the same conversation
   doesn't raise, and the global flag stays off.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.settings import (
    _PRIVATE_MODE_KEY,
    _PRIVATE_MODE_SESSION_PREFIX,
    router,
)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    # fakeredis rather than a hand-rolled stub: the wipe endpoint drives the
    # L4 session registry with sorted-set commands a get/set/delete stub can't
    # model, and the scope contract depends on them.
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    # Patch BOTH the canonical source and the router-local import; FastAPI's
    # route handlers resolve to the symbol that was imported at module load.
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)
    monkeypatch.setattr("app.routers.settings.get_redis", lambda: fake)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake)
    return TestClient(app), fake


def test_validator_accepts_l4(client):
    tc, fake = client
    r = tc.post("/settings/private-mode", json={"level": 4})
    assert r.status_code == 200
    assert r.json() == {"level": 4}
    assert fake.get(_PRIVATE_MODE_KEY) == "4"


def test_validator_still_accepts_l0_through_l3(client):
    tc, _fake = client
    for level in (0, 1, 2, 3):
        r = tc.post("/settings/private-mode", json={"level": level})
        assert r.status_code == 200
        assert r.json() == {"level": level}


def test_validator_rejects_l5_and_negative(client):
    tc, _fake = client
    for bad in (5, 99, -1):
        r = tc.post("/settings/private-mode", json={"level": bad})
        assert r.status_code == 422


def test_session_wipe_winds_the_global_flag_back_to_zero(client, monkeypatch):
    tc, fake = client
    # WB-45: "wiped" now reflects whether Neo4j was reachable and the
    # orchestrator ran — mock both to a deterministic success so this test
    # stays about the Redis flag-clear, not real Neo4j reachability.
    monkeypatch.setattr("app.routers.settings.get_neo4j", lambda: MagicMock())
    fake_summary = {"conversation_sync_deleted": False}
    monkeypatch.setattr(
        "app.routers.settings.wipe_conversation_state",
        lambda *a, **k: fake_summary,
    )
    fake.set(_PRIVATE_MODE_KEY, "4")
    r = tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "conv-123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "wiped": True,
        "level_after": 0,
        "conversation_id": "conv-123",
        "summary": fake_summary,
    }
    assert fake.get(_PRIVATE_MODE_KEY) == "0"


def test_session_wipe_clears_per_session_override(client):
    tc, fake = client
    session_key = f"{_PRIVATE_MODE_SESSION_PREFIX}conv-abc"
    fake.set(session_key, "4")
    fake.set(_PRIVATE_MODE_KEY, "4")

    r = tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "conv-abc"},
    )
    assert r.status_code == 200
    assert fake.get(session_key) is None
    assert fake.get(_PRIVATE_MODE_KEY) == "0"


def test_session_wipe_is_idempotent(client):
    """Re-firing the wipe must not raise — sendBeacon may retry on flaky networks."""
    tc, fake = client
    r1 = tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "conv-x"},
    )
    r2 = tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "conv-x"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert fake.get(_PRIVATE_MODE_KEY) == "0"


def test_session_wipe_rejects_missing_conversation_id(client):
    tc, _fake = client
    r = tc.post("/settings/private-mode/session-wipe", json={})
    assert r.status_code == 422


def test_session_wipe_rejects_overlong_conversation_id(client):
    tc, _fake = client
    r = tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "x" * 200},
    )
    assert r.status_code == 422


def test_session_wipe_does_not_touch_other_sessions(client):
    """A wipe scoped to conv-A must NOT clear conv-B's session key."""
    tc, fake = client
    fake.set(f"{_PRIVATE_MODE_SESSION_PREFIX}conv-A", "4")
    fake.set(f"{_PRIVATE_MODE_SESSION_PREFIX}conv-B", "4")

    tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": "conv-A"},
    )
    assert fake.get(f"{_PRIVATE_MODE_SESSION_PREFIX}conv-A") is None
    assert fake.get(f"{_PRIVATE_MODE_SESSION_PREFIX}conv-B") == "4"
