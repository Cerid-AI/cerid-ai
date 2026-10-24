# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A file dropped in a chat knows its chat (forget spec §6.6), so deleting the
chat can offer it."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.routers import upload
from tests.test_upload_quick_capture import _NOTE, _make_client, deps  # noqa: F401 — the fixture

AID = "a" * 64


def test_an_upload_from_a_chat_is_linked_to_that_chat(deps):  # noqa: F811
    with patch("app.db.neo4j.link_upload") as link, patch("app.deps.get_neo4j", return_value="driver"):
        resp = _make_client().post("/upload", files={"file": _NOTE}, params={"conversation_id": "conv-1"})
    assert resp.status_code == 200
    link.assert_called_once_with("driver", "art-quick-1", "conv-1", existing=False)


def test_an_upload_without_a_chat_links_nothing(deps):  # noqa: F811
    with patch("app.db.neo4j.link_upload") as link:
        _make_client().post("/upload", files={"file": _NOTE})
    link.assert_not_called()


@pytest.mark.parametrize("status,existing", [("success", False), ("duplicate", True), ("updated", True)])
def test_content_the_knowledge_base_already_held_is_linked_as_existing(status, existing):
    with patch("app.db.neo4j.link_upload") as link, patch("app.deps.get_neo4j", return_value="driver"), \
         patch("core.forget.registry.is_forgotten", return_value=False):
        upload._link_to_conversation({"artifact_id": AID, "status": status}, "conv-1")
    link.assert_called_once_with("driver", AID, "conv-1", existing=existing)


@pytest.mark.parametrize("result,cid,forgotten", [
    ({"artifact_id": AID, "status": "success"}, "conv-1", True),    # the chat is forgotten
    ({"artifact_id": AID, "status": "success"}, "../etc", False),   # not a conversation id
    ({"artifact_id": AID, "status": "error"}, "conv-1", False),     # nothing was stored
    ({"status": "success"}, "conv-1", False),                       # no artifact
])
def test_nothing_is_linked_to_a_forgotten_or_invalid_chat_or_a_failed_upload(result, cid, forgotten):
    with patch("app.db.neo4j.link_upload") as link, patch("core.forget.registry.is_forgotten", return_value=forgotten):
        upload._link_to_conversation(result, cid)
    link.assert_not_called()


def test_a_link_that_fails_never_fails_the_stored_upload():
    with patch("app.db.neo4j.link_upload", side_effect=RuntimeError("neo4j down")), \
         patch("app.deps.get_neo4j"), patch("core.forget.registry.is_forgotten", return_value=False):
        upload._link_to_conversation({"artifact_id": AID, "status": "success"}, "conv-1")


def test_nothing_is_linked_under_private_mode():
    with patch("app.db.neo4j.link_upload") as link, patch("core.forget.registry.is_forgotten", return_value=False), \
         patch("app.services.private_mode.saves_blocked", return_value=True):
        upload._link_to_conversation({"artifact_id": AID, "status": "success"}, "conv-1")
    link.assert_not_called()
