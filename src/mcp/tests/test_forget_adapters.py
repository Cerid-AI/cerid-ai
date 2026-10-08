# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Each conversation adapter touches exactly its own store."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.forget.adapters import (
    CLAIMS,
    CONVERSATION_ADAPTERS,
    RedisKeysAdapter,
    SyncFileAdapter,
    TranscriptsAdapter,
)
from core.forget.registry import Subject

S = Subject("conversation", "c1")


def _chroma_with(rows):
    chroma = MagicMock()
    chroma.get_or_create_collection.return_value.get.return_value = {"metadatas": rows}
    return chroma


def test_purge_order_is_derived_first_record_last():
    assert [a.name for a in CONVERSATION_ADAPTERS] == [
        "transcripts", "verification_report_graph", "redis_conversation_keys", "sync_file",
    ]


def test_transcripts_hide_archives_only_chat_artifacts_with_the_forget_reason():
    chroma = _chroma_with([
        {"artifact_id": "a1", "filename": "chat_c1_20261007"},
        {"artifact_id": "m1", "filename": "memory_fact_c1"},
        {"artifact_id": "a1", "filename": "chat_c1_20261007"},
    ])
    with patch("app.deps.get_chroma", return_value=chroma), \
         patch("app.services.content_lifecycle.hide_content") as hide:
        TranscriptsAdapter().hide(S, "fg_x")
    hide.assert_called_once_with("a1", extra_props={"archived_reason": "forget:fg_x"}, only_if_visible=True)


def test_transcripts_restore_clears_only_this_forgets_archive():
    chroma = _chroma_with([{"artifact_id": "a1", "filename": "chat_c1_20261007"}])
    with patch("app.deps.get_chroma", return_value=chroma), \
         patch("app.services.content_lifecycle.unhide_content") as unhide:
        TranscriptsAdapter().restore(S, "fg_x")
    unhide.assert_called_once_with("a1", reason="forget:fg_x")


def test_redis_adapter_deletes_the_four_keys_and_the_l4_membership():
    redis = MagicMock()
    redis.delete.return_value = 3
    redis.zrem.return_value = 1
    with patch("app.deps.get_redis", return_value=redis):
        result = RedisKeysAdapter().purge(S)
    deleted = set(redis.delete.call_args.args)
    assert deleted == {"hall:c1", "conv:c1:metrics", "conv:c1:sentiment", "cerid:private_mode:session:c1"}
    redis.zrem.assert_called_once_with("cerid:private_mode:l4_sessions", "c1")
    assert result.removed == 4


def test_every_redis_key_claimed_by_the_redis_adapter_is_one_it_deletes():
    claimed = {
        pattern.removeprefix("redis:").replace("{cid}", "c1")
        for pattern, owner in CLAIMS.items()
        if owner == "redis_conversation_keys" and pattern.startswith("redis:")
    }
    redis = MagicMock()
    redis.delete.return_value = 0
    redis.zrem.return_value = 0
    with patch("app.deps.get_redis", return_value=redis):
        RedisKeysAdapter().purge(S)
    assert claimed and claimed <= set(redis.delete.call_args.args)


def test_sync_file_adapter_unlinks_the_conversation(tmp_path, monkeypatch):
    from app.sync.user_state import read_conversation, write_conversation
    monkeypatch.setattr("config.SYNC_DIR", str(tmp_path))
    write_conversation(str(tmp_path), {"id": "c1", "messages": []})
    assert SyncFileAdapter().purge(S).removed == 1
    assert read_conversation(str(tmp_path), "c1") == {}
    assert SyncFileAdapter().purge(S).removed == 0


def _redis():
    import fakeredis
    return fakeredis.FakeRedis()


def _hidden_by_forget():
    from app.services.content_lifecycle import hide_content
    from tests.helpers.fake_neo4j import _FakeNeo4jDriver
    neo4j = _FakeNeo4jDriver().add_artifact("a1", chunk_ids=["a1_chunk_0"], domain="conversations")
    hide_content("a1", neo4j=neo4j, redis=_redis(), extra_props={"archived_reason": "forget:fg_x"},
                 only_if_visible=True)
    return neo4j


def test_restoring_a_forget_unhides_what_it_hid():
    from app.services.content_lifecycle import unhide_content
    neo4j = _hidden_by_forget()
    assert unhide_content("a1", reason="forget:fg_x", neo4j=neo4j, redis=_redis()) is True
    assert not neo4j.nodes["a1"].get("archived")


def test_restoring_a_forget_leaves_a_later_quarantine_in_place():
    from app.services.content_lifecycle import hide_content, unhide_content
    neo4j = _hidden_by_forget()
    hide_content("a1", neo4j=neo4j, redis=_redis(),
                 extra_props={"purge_after": "2026-11-07T00:00:00+00:00", "quarantine_reason": "bad source"})
    assert unhide_content("a1", reason="forget:fg_x", neo4j=neo4j, redis=_redis()) is False
    assert neo4j.nodes["a1"]["archived"] is True
    assert neo4j.nodes["a1"]["quarantine_reason"] == "bad source"


def test_restoring_a_forget_leaves_a_later_soft_delete_in_place():
    from app.services.content_lifecycle import hide_content, unhide_content
    neo4j = _hidden_by_forget()
    hide_content("a1", neo4j=neo4j, redis=_redis())
    assert unhide_content("a1", reason="forget:fg_x", neo4j=neo4j, redis=_redis()) is False
    assert neo4j.nodes["a1"]["archived"] is True


def test_a_forget_hide_never_takes_over_an_earlier_archive():
    from app.services.content_lifecycle import hide_content, unhide_content
    from tests.helpers.fake_neo4j import _FakeNeo4jDriver
    neo4j = _FakeNeo4jDriver().add_artifact("a1", chunk_ids=["a1_chunk_0"], archived=True)
    assert hide_content("a1", neo4j=neo4j, redis=_redis(), extra_props={"archived_reason": "forget:fg_x"},
                        only_if_visible=True) is False
    assert unhide_content("a1", reason="forget:fg_x", neo4j=neo4j, redis=_redis()) is False
    assert neo4j.nodes["a1"]["archived"] is True
