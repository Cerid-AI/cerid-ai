# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The L4 session wipe forgets the tab's conversations through the forget engine.

One deletion path (spec §6.1): the wipe used to run its own orchestrator
against a synthetic per-tab id, which erased nothing. It now takes the tab's
session id (to release the L4 registration) and the real conversation ids (to
forget permanently, with what each produced).
"""
from __future__ import annotations

import importlib
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import settings as settings_router
from app.routers.settings import _PRIVATE_MODE_KEY, _PRIVATE_MODE_SESSION_PREFIX, router
from core.forget.registry import Subject


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)
    monkeypatch.setattr("app.routers.settings.get_redis", lambda: fake)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake)
    return TestClient(app), fake


def _preview(cid: str, forgetting: frozenset[str] = frozenset()) -> dict[str, Any]:
    return {"groups": [
        {"key": "transcripts", "default": "always", "items": [{"kind": "artifact", "id": f"t-{cid}"}]},
        {"key": "memories", "default": "checked", "items": [
            {"kind": "artifact", "id": f"mem-{cid}", "shared_with": 0},
            {"kind": "artifact", "id": f"shared-{cid}", "shared_with": 1, "default": "unchecked"},
        ]},
        {"key": "summary", "default": "checked", "items": []},
        {"key": "verified_memories", "default": "checked", "items": [{"kind": "memory", "id": f"vm-{cid}"}]},
        {"key": "cited_documents", "default": "unchecked", "items": [{"kind": "artifact", "id": "kb-doc"}]},
    ]}


def test_wipe_forgets_each_conversation_with_what_it_produced(client, monkeypatch):
    tc, _ = client
    calls: list[tuple[list[Subject], str]] = []

    def forget_permanently(subjects, *, requested_by, user_id=""):
        calls.append((subjects, requested_by))
        return {"forget_id": "fg_" + "1" * 16, "adapters": {"x": {"status": "done", "removed": 1}}}

    monkeypatch.setattr("app.services.forget.preview.preview_conversation", _preview)
    monkeypatch.setattr("app.services.forget.engine.forget_permanently", forget_permanently)
    r = tc.post("/settings/private-mode/session-wipe",
                json={"session_id": "tab-1", "conversation_ids": ["c-1", "c-2"]})
    assert r.status_code == 200
    body = r.json()
    assert body["wiped"] is True and body["forgotten"] == 2 and body["forget_id"] == "fg_" + "1" * 16
    (subjects, requested_by), = calls
    assert requested_by == "private_wipe"
    assert {(s.kind, s.id) for s in subjects} == {
        ("conversation", "c-1"), ("artifact", "mem-c-1"), ("memory", "vm-c-1"),
        ("conversation", "c-2"), ("artifact", "mem-c-2"), ("memory", "vm-c-2"),
    }, "transcripts go with the conversation; shared memories and cited documents stay"


def test_wipe_with_a_failed_preview_still_forgets_the_conversation(client, monkeypatch):
    tc, _ = client
    seen: list[list[Subject]] = []

    def broken(cid, forgetting=frozenset()):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr("app.services.forget.preview.preview_conversation", broken)
    monkeypatch.setattr(
        "app.services.forget.engine.forget_permanently",
        lambda subjects, **k: seen.append(subjects) or {"forget_id": "fg_x", "adapters": {}},
    )
    r = tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-1", "conversation_ids": ["c-1"]})
    assert r.status_code == 200
    assert [(s.kind, s.id) for s in seen[0]] == [("conversation", "c-1")]


def test_a_pending_store_reports_not_wiped(client, monkeypatch):
    tc, _ = client
    monkeypatch.setattr("app.services.forget.preview.preview_conversation", lambda cid, forgetting=frozenset(): {"groups": []})
    monkeypatch.setattr(
        "app.services.forget.engine.forget_permanently",
        lambda subjects, **k: {"forget_id": "fg_x", "adapters": {"artifacts": {"status": "pending"}}},
    )
    r = tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-1", "conversation_ids": ["c-1"]})
    assert r.json()["wiped"] is False


def test_wipe_releases_only_the_closing_tab(client, monkeypatch):
    tc, fake = client
    monkeypatch.setattr(settings_router, "_forget_private_conversations", lambda cids: (None, True))
    for tab in ("tab-A", "tab-B"):
        assert tc.post("/settings/private-mode", json={"level": 4, "session_id": tab}).status_code == 200
    assert fake.get(f"{_PRIVATE_MODE_SESSION_PREFIX}tab-A") == "4"

    r = tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-A", "conversation_ids": []})
    assert r.json()["level_after"] == 4 and fake.get(_PRIVATE_MODE_KEY) == "4"
    r = tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-B"})
    assert r.json()["level_after"] == 0 and fake.get(_PRIVATE_MODE_KEY) == "0"


def test_wipe_validates_ids(client, monkeypatch):
    tc, _ = client
    monkeypatch.setattr(settings_router, "_forget_private_conversations", lambda cids: (None, True))
    assert tc.post("/settings/private-mode/session-wipe",
                   json={"session_id": "tab-1", "conversation_ids": ["../etc"]}).status_code == 400
    assert tc.post("/settings/private-mode/session-wipe",
                   json={"session_id": "tab-1", "conversation_ids": [f"c-{i}" for i in range(51)]}).status_code == 422
    assert tc.post("/settings/private-mode/session-wipe", json={}).status_code == 422


def test_legacy_conversation_id_is_both_the_session_and_a_conversation(client, monkeypatch):
    tc, fake = client
    seen: list[list[str]] = []
    monkeypatch.setattr(settings_router, "_forget_private_conversations", lambda cids: seen.append(cids) or (None, True))
    tc.post("/settings/private-mode", json={"level": 4, "conversation_id": "conv-1"})
    r = tc.post("/settings/private-mode/session-wipe", json={"conversation_id": "conv-1"})
    assert r.status_code == 200 and seen == [["conv-1"]]
    assert fake.get(f"{_PRIVATE_MODE_SESSION_PREFIX}conv-1") is None


def test_the_old_orchestrator_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.session_wipe")


def _multi_user_client(monkeypatch, role):
    app = FastAPI()

    @app.middleware("http")
    async def who(request, call_next):
        request.state.user_id = "u1"
        request.state.role = role
        return await call_next(request)

    app.include_router(router)
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr("app.routers.settings.get_redis", lambda: fake)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake)
    monkeypatch.setattr("config.CERID_MULTI_USER", True)
    return TestClient(app), fake


def test_multi_user_member_wipe_releases_the_tab_but_forgets_nothing(monkeypatch):
    """Conversations carry no owner in multi-user mode, so a member's wipe must
    not become a way to erase someone else's conversation by id."""
    seen: list[list[str]] = []
    monkeypatch.setattr(settings_router, "_forget_private_conversations", lambda cids: seen.append(cids) or (None, True))
    tc, fake = _multi_user_client(monkeypatch, "member")
    tc.post("/settings/private-mode", json={"level": 4, "session_id": "tab-1"})
    r = tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-1", "conversation_ids": ["c-other"]})
    assert r.status_code == 200 and r.json()["forgotten"] == 0 and seen == []
    assert fake.get(f"{_PRIVATE_MODE_SESSION_PREFIX}tab-1") is None


def test_multi_user_admin_wipe_forgets(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(settings_router, "_forget_private_conversations", lambda cids: seen.append(cids) or (None, True))
    tc, _ = _multi_user_client(monkeypatch, "admin")
    r = tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-1", "conversation_ids": ["c-1"]})
    assert r.json()["forgotten"] == 1 and seen == [["c-1"]]


def test_a_memory_shared_only_by_conversations_in_the_same_wipe_is_forgotten(client, monkeypatch):
    tc, _ = client
    seen: list[frozenset[str]] = []

    def preview(cid, forgetting=frozenset()):
        seen.append(forgetting)
        return {"groups": []}

    monkeypatch.setattr("app.services.forget.preview.preview_conversation", preview)
    monkeypatch.setattr("app.services.forget.engine.forget_permanently",
                        lambda subjects, **k: {"forget_id": "fg_x", "adapters": {}})
    tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-1", "conversation_ids": ["c-1", "c-2"]})
    assert seen == [frozenset({"c-1", "c-2"})] * 2


def test_multi_user_member_wipe_reports_not_wiped(monkeypatch):
    monkeypatch.setattr(settings_router, "_forget_private_conversations", lambda cids: (None, True))
    tc, _ = _multi_user_client(monkeypatch, "member")
    r = tc.post("/settings/private-mode/session-wipe", json={"session_id": "tab-1", "conversation_ids": ["c-1"]})
    assert r.json()["wiped"] is False
