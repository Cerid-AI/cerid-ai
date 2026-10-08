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
