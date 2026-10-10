# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Each conversation adapter touches exactly its own store."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.forget.adapters import (
    ADAPTERS,
    CLAIMS,
    OUT_OF_REACH,
    ArtifactAdapter,
    ConversationNodeAdapter,
    RedisKeysAdapter,
    SyncFileAdapter,
    TranscriptsAdapter,
    VerifiedMemoryAdapter,
)
from core.forget.registry import Subject

S = Subject("conversation", "c1")


def _chroma_with(rows):
    chroma = MagicMock()
    chroma.get_or_create_collection.return_value.get.return_value = {"metadatas": rows}
    return chroma


def test_purge_order_is_derived_first_record_last():
    assert [a.name for a in ADAPTERS] == [
        "artifacts", "chunks", "verified_memories", "transcripts", "verification_report_graph",
        "redis_conversation_keys", "sync_file", "conversation_node",
    ]


def test_transcripts_hide_archives_only_chat_artifacts_with_the_forget_reason():
    chroma = _chroma_with([
        {"artifact_id": "a1", "filename": "chat_c1_20261007"},
        {"artifact_id": "m1", "filename": "memory_fact_c1"},
        {"artifact_id": "a1", "filename": "chat_c1_20261007"},
    ])
    with patch("app.deps.get_chroma", return_value=chroma), \
         patch("app.services.content_lifecycle.hide_content") as hide, \
         patch("app.services.derived.mark_derived_stale") as mark:
        TranscriptsAdapter().hide(S, "fg_x")
    hide.assert_called_once_with("a1", extra_props={"archived_reason": "forget:fg_x"}, only_if_visible=True)
    # transcripts are extracted like any document: their pages go before them
    assert list(mark.call_args.args[0]) == ["a1"] and mark.call_args.kwargs == {"forget": "trash"}


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


class _Session:
    """Records every Cypher statement; ``replies`` maps a substring to the row .single() returns."""

    def __init__(self, replies=None):
        self.queries: list[tuple[str, dict]] = []
        self.replies = replies or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.queries.append((query, params))
        result = MagicMock()
        row = next((r for key, r in self.replies.items() if key in query), None)
        result.single.return_value = row
        return result


def _driver(session):
    driver = MagicMock()
    driver.session.return_value = session
    return driver


A = Subject("artifact", "a" * 64)
M = Subject("memory", "11111111-2222-3333-4444-555555555555")


def test_artifact_hide_and_restore_use_the_lifecycle_coordinator_with_the_forget_reason():
    with patch("app.services.content_lifecycle.hide_content") as hide, \
         patch("app.services.content_lifecycle.unhide_content") as unhide, \
         patch("app.services.forget.adapters._restoring", return_value=frozenset({A.id})), \
         patch("app.services.forget.adapters.settle", return_value="prev") as settle, \
         patch("app.services.derived.mark_derived_stale") as mark:
        ArtifactAdapter().hide(A, "fg_x")
        ArtifactAdapter().restore(A, "fg_x")
    hide.assert_called_once_with(A.id, extra_props={"archived_reason": "forget:fg_x"}, only_if_visible=True)
    unhide.assert_called_once_with(A.id, reason="forget:fg_x")
    # the lineage is recomputed each time: the version before it is current while it is in the Trash
    assert settle.call_args_list[0].args == (A.id,)
    assert settle.call_args_list[1].kwargs == {"restoring": frozenset({A.id})}
    # what was written from it, or from the version now current, is refreshed; a forget at once
    assert mark.call_args_list[0].args == ([A.id, "prev"],) and mark.call_args_list[0].kwargs == {"forget": "trash"}
    assert mark.call_args_list[1].args == ([A.id, "prev"],) and mark.call_args_list[1].kwargs == {}


def test_artifact_purge_sweeps_facts_removes_then_settles_the_lineage():
    session = _Session({"[:FACT]->(f:Fact)": {"n": 2}})
    order: list[str] = []
    removal = MagicMock(found=True)

    def remove(aid):
        order.append(f"remove:{aid}")
        assert len(session.queries) == 1, "facts must be swept while the edges still exist"
        return removal

    with patch("app.deps.get_neo4j", return_value=_driver(session)), \
         patch("core.lineage.writer.lineage_of", return_value="lin-1"), \
         patch("app.services.forget.adapters.settle",
               side_effect=lambda aid, lineage_id: order.append(f"settle:{aid}:{lineage_id}")), \
         patch("app.services.derived.mark_derived_stale",
               side_effect=lambda ids, forget: order.append(f"stale:{','.join(ids)}:{forget}")), \
         patch("app.services.content_lifecycle.remove_content", side_effect=remove):
        result = ArtifactAdapter().purge(A)
    fact_q = session.queries[0][0]
    assert "NOT EXISTS" in fact_q and "DETACH DELETE f" in fact_q
    assert "(f:Fact {source_artifact_id: $aid})" in fact_q  # found by source even without the edge
    # the lineage is read, and what was written from it marked stale, before the node and its edges go
    assert order == [f"stale:{A.id}:erase", f"remove:{A.id}", f"settle:{A.id}:lin-1"]
    assert result.removed == 3 and result.detail == {"facts": 2}


def test_verified_memory_hide_marks_the_node_and_drops_the_recall_document():
    session = _Session()
    coll = MagicMock()
    with patch("app.deps.get_neo4j", return_value=_driver(session)), \
         patch("app.services.forget.adapters._verified_collection", return_value=coll), \
         patch("app.services.forget.adapters.settle") as settle, \
         patch("app.services.content_lifecycle.invalidate_caches"):
        VerifiedMemoryAdapter().hide(M, "fg_x")
    query, params = session.queries[0]
    assert "m.status = 'forgotten'" in query and params == {"mid": M.id, "fid": "fg_x"}
    coll.delete.assert_called_once_with(ids=[f"verified_memory_{M.id}"])
    settle.assert_called_once_with(M.id)


def test_verified_memory_restore_rebuilds_the_document_from_the_node_text():
    session = _Session({"m.text AS text": {"text": "Water boils at 100 C at sea level."}})
    coll = MagicMock()
    with patch("app.deps.get_neo4j", return_value=_driver(session)), \
         patch("app.services.forget.adapters._verified_collection", return_value=coll), \
         patch("app.services.forget.adapters._restoring", return_value=frozenset()), \
         patch("app.services.forget.adapters.settle"), \
         patch("app.services.content_lifecycle.invalidate_caches"):
        VerifiedMemoryAdapter().restore(M, "fg_x")
    assert "forget_id: $fid" in session.queries[0][0]
    assert "SET m.status = 'active'" in session.queries[1][0], "the node is reactivated after the document"
    kwargs = coll.upsert.call_args.kwargs
    assert kwargs["ids"] == [f"verified_memory_{M.id}"]
    assert kwargs["documents"] == ["Water boils at 100 C at sea level."]
    meta = kwargs["metadatas"][0]
    assert meta["artifact_id"] == M.id and meta["memory_source_type"] == "verification"
    assert meta["filename"] == f"verified_fact_{M.id[:8]}"


def test_verified_memory_restore_of_another_forget_changes_nothing():
    session = _Session()  # no row: the node was hidden by a different forget
    coll = MagicMock()
    with patch("app.deps.get_neo4j", return_value=_driver(session)), \
         patch("app.services.forget.adapters._verified_collection", return_value=coll):
        VerifiedMemoryAdapter().restore(M, "fg_other")
    coll.upsert.assert_not_called()


def test_verified_memory_purge_deletes_document_and_node():
    session = _Session({"DETACH DELETE m": {"n": 1}})
    coll = MagicMock()
    with patch("app.deps.get_neo4j", return_value=_driver(session)), \
         patch("app.services.forget.adapters._verified_collection", return_value=coll), \
         patch("core.lineage.writer.lineage_of", return_value=""), \
         patch("app.services.forget.adapters.settle"), \
         patch("app.services.content_lifecycle.invalidate_caches"):
        result = VerifiedMemoryAdapter().purge(M)
    coll.delete.assert_called_once_with(ids=[f"verified_memory_{M.id}"])
    assert result.removed == 1


def test_conversation_node_is_deleted_only_when_nothing_kept_hangs_off_it():
    session = _Session({"Conversation": {"n": 0}})
    with patch("app.deps.get_neo4j", return_value=_driver(session)):
        result = ConversationNodeAdapter().purge(S)
    query, params = session.queries[0]
    assert "WHERE NOT ()-[:EXTRACTED_FROM]->(c)" in query and params == {"cid": "c1"}
    assert result.removed == 0


def test_phase_two_stores_are_claimed_not_out_of_reach():
    for pattern, owner in {
        "chroma:conversations:memory_*": "artifacts",
        "chroma:conversations:session_summary_*": "artifacts",
        "neo4j:Conversation.id": "conversation_node",
        "neo4j:Memory (verified)": "verified_memories",
    }.items():
        assert CLAIMS[pattern] == owner
        assert pattern not in OUT_OF_REACH


def test_verified_memory_restore_rebuilds_the_document_before_reactivating_the_node():
    """If the rebuild fails, the node stays forgotten so a retry can still find it."""
    session = _Session({"m.text AS text": {"text": "fact"}})
    coll = MagicMock()
    coll.upsert.side_effect = RuntimeError("chroma down")
    with patch("app.deps.get_neo4j", return_value=_driver(session)), \
         patch("app.services.forget.adapters._verified_collection", return_value=coll), \
         patch("app.services.content_lifecycle.invalidate_caches"):
        try:
            VerifiedMemoryAdapter().restore(M, "fg_x")
        except RuntimeError:
            pass
    assert not any("SET m.status = 'active'" in q for q, _ in session.queries)


def test_only_a_retention_purge_leaves_pages_alone(tmp_path, monkeypatch):
    """A person's forget of an earlier version may still be in an unrefreshed page."""
    from app.services.forget import adapters
    from core.forget.registry import RETENTION, Entry
    from tests.helpers.forget import isolate_forget

    reg = isolate_forget(monkeypatch, tmp_path)
    reg.append([
        Entry("fg_r", Subject("chunk", "a_old"), "trashed", "2026-10-01T00:00:00Z", "m1", RETENTION),
        Entry("fg_p", Subject("chunk", "a_earlier"), "trashed", "2026-10-01T00:00:00Z", "m1", "ui"),
    ])
    assert adapters._feeds_pages("a_old") is False
    assert adapters._feeds_pages("a_earlier") is True


def test_a_marking_failure_never_leaves_transcripts_readable():
    """The engine retries a hide that raised; the transcripts must be hidden already."""
    import pytest

    chroma = _chroma_with([{"artifact_id": "a1", "filename": "chat_c1_20261007"}])
    with patch("app.deps.get_chroma", return_value=chroma), \
         patch("app.services.content_lifecycle.hide_content") as hide, \
         patch("app.services.derived.mark_derived_stale", side_effect=RuntimeError("neo4j down")):
        with pytest.raises(RuntimeError):
            TranscriptsAdapter().hide(S, "fg_x")
    hide.assert_called_once()
