# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""L4 session-wipe scope (F021).

``SessionWipeRequest`` promises the wipe is scoped to one conversation
"so a wipe from one L4 tab doesn't affect another open tab's state", but
the handler deleted the GLOBAL ``cerid:private_mode:global`` key, which
``get_private_mode_level`` resolves to 0. Closing one private tab dropped
every other tab — and every direct API/SDK/MCP caller — back to level 0
with no user action and no UI signal.

These tests pin the scope contract: a wipe releases its own session, and
the global flag only drops when the last live L4 session is gone. They
also pin the store shape — an explicit ``"0"``, never a deleted key, per
the reset endpoint's own E1 R13 note (deletion makes
``seed_private_mode_from_env`` re-apply the env level on restart).
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
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)
    monkeypatch.setattr("app.routers.settings.get_redis", lambda: fake)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake)
    monkeypatch.setattr("app.routers.settings.get_neo4j", lambda: MagicMock())
    monkeypatch.setattr(
        "app.routers.settings.wipe_conversation_state", lambda *a, **k: {},
    )
    return TestClient(app), fake


def _enter_l4(tc, conversation_id):
    r = tc.post(
        "/settings/private-mode",
        json={"level": 4, "conversation_id": conversation_id},
    )
    assert r.status_code == 200
    return r


def _wipe(tc, conversation_id):
    return tc.post(
        "/settings/private-mode/session-wipe",
        json={"conversation_id": conversation_id},
    )


def test_wipe_leaves_other_l4_sessions_at_level_4(client):
    tc, fake = client
    _enter_l4(tc, "tab-A")
    _enter_l4(tc, "tab-B")

    r = _wipe(tc, "tab-A")

    assert r.status_code == 200
    assert fake.get(_PRIVATE_MODE_KEY) == "4", "tab-B is still open at L4"
    assert r.json()["level_after"] == 4
    assert fake.get(f"{_PRIVATE_MODE_SESSION_PREFIX}tab-A") is None
    assert fake.get(f"{_PRIVATE_MODE_SESSION_PREFIX}tab-B") == "4"


def test_last_session_out_turns_private_mode_off(client):
    tc, fake = client
    _enter_l4(tc, "tab-A")
    _enter_l4(tc, "tab-B")

    _wipe(tc, "tab-A")
    r = _wipe(tc, "tab-B")

    assert r.json()["level_after"] == 0
    assert fake.get(_PRIVATE_MODE_KEY) == "0"


def test_wipe_stores_explicit_zero_rather_than_deleting_the_key(client):
    """E1 R13: a deleted key reads as 'unset', so seed_private_mode_from_env
    re-applies the boot env level on the next restart, resurrecting a level
    the user just wiped their way out of."""
    tc, fake = client
    fake.set(_PRIVATE_MODE_KEY, "4")

    _wipe(tc, "conv-1")

    assert fake.exists(_PRIVATE_MODE_KEY) == 1
    assert fake.get(_PRIVATE_MODE_KEY) == "0"


def test_dropping_below_l4_releases_the_session(client):
    """A tab that steps down from L4 must stop holding the global flag up."""
    tc, fake = client
    _enter_l4(tc, "tab-A")
    _enter_l4(tc, "tab-B")

    tc.post("/settings/private-mode", json={"level": 1, "conversation_id": "tab-B"})
    _wipe(tc, "tab-A")

    assert fake.get(_PRIVATE_MODE_KEY) == "0"


def test_reset_endpoint_clears_the_session_registry(client):
    """The explicit off switch is authoritative — a later beacon from a stale
    tab must not find a registry entry that keeps private mode pinned on."""
    tc, fake = client
    _enter_l4(tc, "tab-A")
    _enter_l4(tc, "tab-B")

    assert tc.delete("/settings/private-mode").status_code == 200
    assert fake.get(_PRIVATE_MODE_KEY) == "0"

    tc.post("/settings/private-mode", json={"level": 4})
    _wipe(tc, "tab-A")
    assert fake.get(_PRIVATE_MODE_KEY) == "0"
