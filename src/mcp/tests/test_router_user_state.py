# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the user-state API router."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.user_state import _sync_dir, router  # noqa: F401


@pytest.fixture()
def sync_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def app(sync_dir: Path) -> FastAPI:
    _app = FastAPI()
    _app.include_router(router)
    with patch("routers.user_state._sync_dir", return_value=str(sync_dir)):
        yield _app


@pytest.fixture()
def client(app: FastAPI, sync_dir: Path) -> TestClient:
    with patch("routers.user_state._sync_dir", return_value=str(sync_dir)):
        yield TestClient(app)


# -- GET /user-state ----------------------------------------------------------


def test_get_summary_empty(client: TestClient):
    resp = client.get("/user-state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["settings"] == {}
    assert data["preferences"] == {}
    assert data["conversation_ids"] == []


# -- POST + GET conversations -------------------------------------------------


def test_save_and_list_conversations(client: TestClient):
    resp = client.post("/user-state/conversations", json={"id": "c1", "title": "Hello"})
    assert resp.status_code == 200
    assert resp.json() == {"saved": "c1"}

    resp = client.get("/user-state/conversations")
    assert resp.status_code == 200
    convs = resp.json()
    assert len(convs) == 1
    assert convs[0]["id"] == "c1"
    assert convs[0]["title"] == "Hello"


# -- GET single conversation --------------------------------------------------


def test_get_single_conversation(client: TestClient):
    client.post("/user-state/conversations", json={"id": "c2", "title": "World"})
    resp = client.get("/user-state/conversations/c2")
    assert resp.status_code == 200
    assert resp.json()["id"] == "c2"


def test_get_missing_conversation_404(client: TestClient):
    resp = client.get("/user-state/conversations/nonexistent")
    assert resp.status_code == 404


# -- POST conversation validation ---------------------------------------------


def test_save_conversation_missing_id(client: TestClient):
    resp = client.post("/user-state/conversations", json={"title": "No ID"})
    assert resp.status_code == 400


# -- DELETE conversation ------------------------------------------------------


def test_delete_conversation(client: TestClient):
    client.post("/user-state/conversations", json={"id": "del1", "title": "Delete me"})
    resp = client.delete("/user-state/conversations/del1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "del1"}

    resp = client.get("/user-state/conversations/del1")
    assert resp.status_code == 404


# -- POST bulk conversations --------------------------------------------------


def test_bulk_save_conversations(client: TestClient):
    body = [
        {"id": "b1", "title": "Bulk 1"},
        {"id": "b2", "title": "Bulk 2"},
        {"id": "b3", "title": "Bulk 3"},
    ]
    resp = client.post("/user-state/conversations/bulk", json=body)
    assert resp.status_code == 200
    assert resp.json() == {"saved": 3}

    resp = client.get("/user-state/conversations")
    assert len(resp.json()) == 3


# -- PATCH preferences --------------------------------------------------------


def test_patch_preferences(client: TestClient):
    resp = client.patch("/user-state/preferences", json={"theme": "dark"})
    assert resp.status_code == 200

    # Verify via summary
    resp = client.get("/user-state")
    prefs = resp.json()["preferences"]
    assert prefs["theme"] == "dark"


def test_patch_preferences_merges(client: TestClient):
    client.patch("/user-state/preferences", json={"theme": "dark"})
    client.patch("/user-state/preferences", json={"fontSize": 14})

    resp = client.get("/user-state")
    prefs = resp.json()["preferences"]
    assert prefs["theme"] == "dark"
    assert prefs["fontSize"] == 14


# -- Summary includes conversation IDs ---------------------------------------


def test_summary_includes_conversation_ids(client: TestClient):
    client.post("/user-state/conversations", json={"id": "s1", "title": "Sum 1"})
    client.post("/user-state/conversations", json={"id": "s2", "title": "Sum 2"})

    resp = client.get("/user-state")
    ids = resp.json()["conversation_ids"]
    assert set(ids) == {"s1", "s2"}


# -- 503 when sync_dir is empty -----------------------------------------------


def test_write_503_when_no_sync_dir():
    _app = FastAPI()
    _app.include_router(router)
    with patch("routers.user_state._sync_dir", return_value=""):
        c = TestClient(_app)
        resp = c.post("/user-state/conversations", json={"id": "x", "title": "y"})
        assert resp.status_code == 503

        resp = c.post("/user-state/conversations/bulk", json=[{"id": "x"}])
        assert resp.status_code == 503

        resp = c.delete("/user-state/conversations/x")
        assert resp.status_code == 503

        resp = c.patch("/user-state/preferences", json={"a": 1})
        assert resp.status_code == 503
