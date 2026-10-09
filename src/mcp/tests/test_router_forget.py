# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""HTTP surface of the forget engine and the conversation routes that use it."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import forget as forget_router
from app.routers import user_state
from app.services.forget import engine
from app.services.forget.adapters import PurgeResult
from tests.helpers.forget import isolate_forget


class Noop:
    def __init__(self, name):
        self.name, self.kinds = name, frozenset({"conversation"})

    def hide(self, s, f):
        return None

    def restore(self, s, f):
        return None

    def purge(self, s):
        return PurgeResult()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr(user_state, "_sync_dir", lambda: str(tmp_path))
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [Noop("x")])
    app = FastAPI()
    app.include_router(user_state.router)
    app.include_router(forget_router.router)
    with patch("app.services.private_mode.get_private_mode_level", lambda *a, **k: 0):
        yield TestClient(app)


def test_delete_trashes_hides_from_list_and_refuses_repost(client):
    client.post("/user-state/conversations", json={"id": "c1", "messages": []})
    resp = client.delete("/user-state/conversations/c1")
    assert resp.status_code == 200 and resp.json()["state"] == "trashed"
    assert client.get("/user-state/conversations").json() == []
    assert client.get("/user-state").json()["conversation_ids"] == []
    assert client.get("/user-state/conversations/c1").status_code == 404
    assert client.post("/user-state/conversations", json={"id": "c1", "messages": []}).status_code == 410
    bulk = client.post("/user-state/conversations/bulk", json=[{"id": "c1"}, {"id": "c2"}]).json()
    assert bulk == {"saved": 1, "gone": ["c1"]}


def test_forgotten_feed_lists_subjects_with_a_cursor(client):
    client.delete("/user-state/conversations/c1")
    body = client.get("/user-state/forgotten").json()
    assert [(i["kind"], i["id"], i["state"]) for i in body["items"]] == [("conversation", "c1", "trashed")]
    assert client.get("/user-state/forgotten", params={"since": body["cursor"]}).json()["items"] == []


def test_forgetting_a_conversation_the_server_never_had_is_still_recorded(client):
    resp = client.post("/forget", json={"subjects": [{"kind": "conversation", "id": "client-only"}], "mode": "trash"})
    assert resp.status_code == 200
    ids = [i["id"] for i in client.get("/user-state/forgotten").json()["items"]]
    assert "client-only" in ids


def test_restore_then_restore_after_purge(client):
    fid = client.post("/forget", json={"subjects": [{"kind": "conversation", "id": "c1"}], "mode": "trash"}).json()["forget_id"]
    assert client.post(f"/forget/{fid}/restore").json() == {"restored": 1}
    fid2 = client.post("/forget", json={"subjects": [{"kind": "conversation", "id": "c1"}], "mode": "permanent"}).json()["forget_id"]
    assert client.post(f"/forget/{fid2}/restore").status_code == 409


def test_only_conversations_are_forgettable_over_http_in_phase_1(client):
    resp = client.post("/forget", json={"subjects": [{"kind": "artifact", "id": "a1"}], "mode": "trash"})
    assert resp.status_code == 400


def test_a_conversation_id_that_is_not_a_safe_filename_is_refused(client):
    resp = client.post("/forget", json={"subjects": [{"kind": "conversation", "id": "../../x"}], "mode": "trash"})
    assert resp.status_code == 400
    assert client.get("/user-state/forgotten").json()["items"] == []


def test_delete_is_allowed_in_private_mode(client):
    with patch("app.services.private_mode.get_private_mode_level", lambda *a, **k: 3):
        assert client.delete("/user-state/conversations/c1").status_code == 200


def test_without_a_sync_dir_forget_is_a_precondition_failure(client, monkeypatch):
    monkeypatch.setattr("config.SYNC_DIR", "")
    resp = client.post("/forget", json={"subjects": [{"kind": "conversation", "id": "c1"}], "mode": "trash"})
    assert resp.status_code == 412


def test_preview_route_returns_the_grouped_preview(client):
    fake = {"subject": {"kind": "conversation", "id": "c-1"}, "title": "", "groups": [], "derived_facts": 0,
            "out_of_reach": []}
    with patch("app.routers.forget.preview_conversation", return_value=fake) as pv:
        r = client.post("/forget/preview", json={"kind": "conversation", "id": "c-1"})
    assert r.status_code == 200 and r.json()["subject"]["id"] == "c-1"
    pv.assert_called_once_with("c-1")


def test_preview_route_rejects_other_kinds_and_bad_ids(client):
    assert client.post("/forget/preview", json={"kind": "artifact", "id": "a" * 64}).status_code == 422
    assert client.post("/forget/preview", json={"kind": "conversation", "id": "../x"}).status_code == 400


def test_forget_accepts_derived_subjects_and_validates_their_ids(client):
    ok = client.post("/forget", json={"subjects": [
        {"kind": "conversation", "id": "c-1"},
        {"kind": "artifact", "id": "a" * 64},
        {"kind": "memory", "id": "11111111-2222-3333-4444-555555555555"},
    ]})
    assert ok.status_code == 200 and ok.json()["state"] == "trashed"
    assert client.post("/forget", json={"subjects": [{"kind": "artifact", "id": "../x"}]}).status_code == 400
    assert client.post("/forget", json={"subjects": [{"kind": "chunk", "id": "a" * 64}]}).status_code == 400


def test_trash_and_receipt_routes(client):
    fid = client.post("/forget", json={"subjects": [{"kind": "conversation", "id": "c-1"}]}).json()["forget_id"]
    with patch("app.services.forget.engine._labels", return_value={}):
        trash = client.get("/forget/trash").json()["items"]
    assert [g["forget_id"] for g in trash] == [fid]
    gone = client.post("/forget", json={"subjects": [{"kind": "conversation", "id": "c-2"}], "mode": "permanent"})
    receipts = client.get("/forget/receipts").json()["items"]
    assert gone.json()["forget_id"] in {r["forget_id"] for r in receipts}
    assert client.get(f"/forget/receipts/{gone.json()['forget_id']}").json()["forget_id"] == gone.json()["forget_id"]
    assert client.get("/forget/receipts/fg_bad").status_code == 400
    assert client.get("/forget/receipts/fg_" + "f" * 16).status_code == 404


@pytest.fixture()
def multi_user(tmp_path: Path, monkeypatch):
    """Multi-user mode with the caller set by a header, standing in for the JWT middleware."""
    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr(user_state, "_sync_dir", lambda: str(tmp_path))
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [Noop("x")])
    monkeypatch.setattr("config.CERID_MULTI_USER", True)
    monkeypatch.setattr(forget_router, "CERID_MULTI_USER", True, raising=False)
    app = FastAPI()

    @app.middleware("http")
    async def who(request, call_next):
        request.state.user_id = request.headers.get("x-test-user", "")
        request.state.role = request.headers.get("x-test-role", "member")
        return await call_next(request)

    app.include_router(forget_router.router)
    with patch("app.services.private_mode.get_private_mode_level", lambda *a, **k: 0):
        yield TestClient(app)


def _as(user, role="member"):
    return {"x-test-user": user, "x-test-role": role}


def test_multi_user_every_forget_route_needs_an_admin(multi_user):
    """Conversations carry no owner in multi-user mode, so a member cannot be
    limited to their own: the forget routes are admin-only there."""
    member = _as("u1")
    conv = {"subjects": [{"kind": "conversation", "id": "c-1"}]}
    assert multi_user.post("/forget", json=conv, headers=member).status_code == 403
    assert multi_user.post("/forget/preview", json={"kind": "conversation", "id": "c-1"},
                           headers=member).status_code == 403
    assert multi_user.get("/forget/trash", headers=member).status_code == 403
    assert multi_user.get("/forget/receipts", headers=member).status_code == 403
    assert multi_user.post("/forget/trash/empty", headers=member).status_code == 403
    fid = multi_user.post("/forget", json=conv, headers=_as("boss", "admin")).json()["forget_id"]
    assert multi_user.post(f"/forget/{fid}/restore", headers=member).status_code == 403
    assert multi_user.get(f"/forget/receipts/{fid}", headers=member).status_code == 403
    assert multi_user.post(f"/forget/{fid}/restore", headers=_as("boss", "admin")).status_code == 200


def test_multi_user_admin_uses_every_route(multi_user):
    admin = _as("boss", "admin")
    art = {"subjects": [{"kind": "artifact", "id": "a" * 64}], "mode": "permanent"}
    gone = multi_user.post("/forget", json=art, headers=admin).json()["forget_id"]
    assert multi_user.get(f"/forget/receipts/{gone}", headers=admin).status_code == 200
    with patch("app.services.forget.engine._labels", return_value={}):
        assert multi_user.get("/forget/trash", headers=admin).status_code == 200
    assert multi_user.post("/forget/trash/empty", headers=admin).status_code == 200


def test_multi_user_member_cannot_purge_through_the_conversation_delete_route(tmp_path, monkeypatch):
    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr(user_state, "_sync_dir", lambda: str(tmp_path))
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [Noop("x")])
    monkeypatch.setattr("config.CERID_MULTI_USER", True)
    app = FastAPI()

    @app.middleware("http")
    async def who(request, call_next):
        request.state.user_id = "u1"
        request.state.role = request.headers.get("x-test-role", "member")
        return await call_next(request)

    app.include_router(user_state.router)
    with patch("app.services.private_mode.get_private_mode_level", lambda *a, **k: 0):
        tc = TestClient(app)
        assert tc.delete("/user-state/conversations/c-1?permanent=true").status_code == 403
        assert tc.delete("/user-state/conversations/c-1?permanent=true",
                         headers={"x-test-role": "admin"}).status_code == 200
