# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Conversation ids must never escape the sync directory (F376)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.user_state import router
from app.sync import user_state as sync_user_state

TRAVERSAL_IDS = [
    "../../../../pwned",
    "..%2F..%2Fpwned",
    "a/../../b",
    "/etc/pwned",
    "",
    "a" * 200,
    "sub/dir",
    ".",
    "..",
]


@pytest.fixture(autouse=True)
def _mock_redis():
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    with patch("app.deps._redis", mock_redis), \
         patch("app.deps.get_redis", return_value=mock_redis), \
         patch("app.services.private_mode.get_private_mode_level", return_value=0):
        yield


@pytest.fixture()
def sync_root(tmp_path: Path) -> Path:
    return tmp_path / "sync"


@pytest.fixture()
def client(sync_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    with patch("app.routers.user_state._sync_dir", return_value=str(sync_root)):
        yield TestClient(app)


class TestSyncLayerRejectsTraversal:
    """The sync layer is the last line of defence — it must validate too."""

    @pytest.mark.parametrize("conv_id", TRAVERSAL_IDS)
    def test_write_conversation_rejects_bad_id(self, conv_id: str, sync_root: Path):
        with pytest.raises(ValueError):
            sync_user_state.write_conversation(str(sync_root), {"id": conv_id, "x": 1})

    @pytest.mark.parametrize("conv_id", TRAVERSAL_IDS)
    def test_read_conversation_rejects_bad_id(self, conv_id: str, sync_root: Path):
        with pytest.raises(ValueError):
            sync_user_state.read_conversation(str(sync_root), conv_id)

    @pytest.mark.parametrize("conv_id", TRAVERSAL_IDS)
    def test_delete_conversation_rejects_bad_id(self, conv_id: str, sync_root: Path):
        with pytest.raises(ValueError):
            sync_user_state.delete_conversation(str(sync_root), conv_id)

    def test_write_does_not_escape_sync_dir(self, sync_root: Path, tmp_path: Path):
        outside = tmp_path / "pwned.json"
        with pytest.raises(ValueError):
            sync_user_state.write_conversation(
                str(sync_root), {"id": "../../pwned", "secret": "x"},
            )
        assert not outside.exists()
        assert not (tmp_path / "pwned.json").exists()

    def test_legal_id_still_round_trips(self, sync_root: Path):
        sync_user_state.write_conversation(str(sync_root), {"id": "conv-1_A", "v": 7})
        assert sync_user_state.read_conversation(str(sync_root), "conv-1_A")["v"] == 7
        sync_user_state.delete_conversation(str(sync_root), "conv-1_A")
        assert sync_user_state.read_conversation(str(sync_root), "conv-1_A") == {}


class TestRouterRejectsTraversal:
    def test_post_conversation_with_traversal_id_is_rejected(
        self, client: TestClient, sync_root: Path, tmp_path: Path,
    ):
        resp = client.post(
            "/user-state/conversations",
            json={"id": "../../../../pwned", "messages": []},
        )
        assert resp.status_code == 400, resp.text
        escaped = list(tmp_path.parent.glob("**/pwned.json"))
        assert escaped == [], f"wrote outside sync dir: {escaped}"

    def test_bulk_conversation_with_traversal_id_is_rejected(
        self, client: TestClient, sync_root: Path, tmp_path: Path,
    ):
        resp = client.post(
            "/user-state/conversations/bulk",
            json=[{"id": "ok-1"}, {"id": "../../../../pwned"}],
        )
        assert resp.status_code == 400, resp.text
        assert list(tmp_path.parent.glob("**/pwned.json")) == []

    def test_delete_conversation_with_traversal_id_is_rejected(
        self, client: TestClient, sync_root: Path, tmp_path: Path,
    ):
        victim = tmp_path / "victim.json"
        victim.write_text("{}", encoding="utf-8")
        resp = client.request(
            "DELETE", "/user-state/conversations/..%2F..%2Fvictim",
        )
        assert resp.status_code in (400, 404), resp.text
        assert victim.exists(), "delete escaped the conversations directory"

    def test_get_conversation_with_traversal_id_is_rejected(self, client: TestClient):
        resp = client.get("/user-state/conversations/..%2F..%2Fsettings")
        assert resp.status_code in (400, 404), resp.text
