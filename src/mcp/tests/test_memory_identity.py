# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""One memory identity (forget spec §6.3): every memory's rows are {id}_{h16},
every memory names its conversation on the node, and nothing reads a
conversation or a type out of a filename fragment."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.retrieval.artifact_rows import (
    LEGACY_VERIFIED_MEMORY_PREFIX,
    is_verified_memory_row,
    verified_memory_id,
    verified_memory_row_id,
)
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection


def test_a_claim_has_one_id_whatever_its_spacing_or_case():
    assert verified_memory_id("The limit is $23,500.") == verified_memory_id("  the limit is   $23,500. ")
    assert verified_memory_id("The limit is $23,500.") != verified_memory_id("The limit is $24,000.")


def test_a_verified_memory_row_is_known_by_its_metadata_in_either_id_form():
    assert is_verified_memory_row(f"{LEGACY_VERIFIED_MEMORY_PREFIX}abc", {})
    assert is_verified_memory_row("abc_0123456789abcdef", {"memory_source_type": "verification"})
    assert not is_verified_memory_row("abc_0123456789abcdef", {"memory_type": "fact"})


@pytest.mark.parametrize("filename,expected", [
    ("memory_action_item_1234abcd_20261009_120000_0", "action_item"),
    ("memory_project_context_1234abcd_20261009_120000_0", "project_context"),
    ("memory_decision_1234abcd_20261009_120000_0", "decision"),
    ("memory_mystery_1234abcd_20261009_120000_0", "unknown"),
])
def test_a_memory_s_type_is_never_cut_short_at_an_underscore(filename, expected):
    from app.routers.memories import _memory_type_of

    assert _memory_type_of(filename) == expected


class _Session:
    def __init__(self, conversations: dict[str, str]):
        self.conversations = conversations
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query: str, **params: Any):
        self.queries.append(query)
        if "UNWIND $ids" in query:
            return [{"id": i, "cid": self.conversations.get(i)} for i in params["ids"]]
        return SimpleNamespace(single=lambda: {"n": 2})


def test_rows_written_before_are_re_keyed_and_told_their_conversation(monkeypatch):
    from app.services import memory_identity_migration as mig

    col = FakeChromaCollection("conversations")
    old, other = "11111111-2222-3333-4444-555555555555", "artifact-row"
    col.upsert(ids=[f"{LEGACY_VERIFIED_MEMORY_PREFIX}{old}"], documents=["Water boils at 100 C."], embeddings=[[1.0, 0.0]],
               metadatas=[{"artifact_id": old, "memory_source_type": "verification"}])
    col.upsert(ids=[f"{other}_0123456789abcdef"], documents=["x"], embeddings=[[0.0, 1.0]],
               metadatas=[{"artifact_id": other, "memory_type": "fact"}])
    monkeypatch.setattr(mig.config, "collection_name", lambda domain: "conversations")
    session = _Session({old: "conv-1"})
    driver = MagicMock()
    driver.session.return_value = session

    counts = mig.migrate_memory_identity(chroma=FakeChromaClient([col]), neo4j=driver)

    new_id = verified_memory_row_id(old, "Water boils at 100 C.")
    assert counts == {"artifact_conversations": 2, "verified_conversations": 2, "verified_rows": 1}
    assert col.metadata_of(new_id)["conversation_id"] == "conv-1"
    assert col.get(ids=[f"{LEGACY_VERIFIED_MEMORY_PREFIX}{old}"])["ids"] == []
    assert col.metadata_of(f"{other}_0123456789abcdef") == {"artifact_id": other, "memory_type": "fact"}
    assert any("SET a.conversation_id = cid" in q for q in session.queries)
    assert any("MERGE (m)-[:EXTRACTED_FROM]->(conv)" in q for q in session.queries)

    session.conversations.clear()
    assert mig.migrate_memory_identity(chroma=FakeChromaClient([col]), neo4j=driver)["verified_rows"] == 0


def test_the_migration_writes_back_no_forgotten_memory_and_keeps_a_newer_row(monkeypatch):
    from app.services import memory_identity_migration as mig

    col = FakeChromaCollection("conversations")
    gone, kept = "gone-memory", "kept-memory"
    for mid in (gone, kept):
        col.upsert(ids=[f"{LEGACY_VERIFIED_MEMORY_PREFIX}{mid}"], documents=[f"claim {mid}"], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": mid, "memory_source_type": "verification", "stale": True}])
    newer = verified_memory_row_id(kept, f"claim {kept}")
    col.upsert(ids=[newer], documents=[f"claim {kept}"], embeddings=[[1.0, 0.0]],
               metadatas=[{"artifact_id": kept, "memory_source_type": "verification", "lineage_id": "L"}])
    monkeypatch.setattr(mig.config, "collection_name", lambda domain: "conversations")
    monkeypatch.setattr(mig.forget_registry, "is_forgotten", lambda kind, i: i == gone)
    driver = MagicMock()
    driver.session.return_value = _Session({})

    mig.migrate_memory_identity(chroma=FakeChromaClient([col]), neo4j=driver)

    assert col.get(ids=[verified_memory_row_id(gone, f"claim {gone}")])["ids"] == []  # not written back
    assert col.metadata_of(newer) == {"artifact_id": kept, "memory_source_type": "verification", "lineage_id": "L"}
    assert not [i for i in col.get()["ids"] if i.startswith(LEGACY_VERIFIED_MEMORY_PREFIX)]


def test_a_memory_without_a_link_is_still_found_by_its_conversation():
    """Older than its node's conversation and with no edge: the filename still
    names the first 8 characters of its conversation."""
    from app.routers.memories import _conversation_memory_queries

    listing, _count = _conversation_memory_queries(None, "abcd1234-0000")
    assert "cid STARTS WITH $convo_prefix" in listing and "left($convo_prefix, 8)" in listing


class _EditSession(_Session):
    def run(self, query: str, **params: Any):
        self.queries.append(query)
        return SimpleNamespace(single=lambda: {
            "id": params.get("memory_id"), "filename": "memory_action_item_1234abcd_20261009_120000_0",
            "summary": "old", "created_at": "2026-10-09T12:00:00Z", "conversation_id": "conv-full-id",
        })


def _edit(monkeypatch, *, multi_user=False, admin=False, forgotten=False, ingested=None, old_meta=None):
    import asyncio

    from app.routers import memories

    session = _EditSession({})
    driver = MagicMock()
    driver.session.return_value = session
    col = FakeChromaCollection(memories.config.collection_name("conversations"))
    col.upsert(
        ids=["art-1_chunk_0"], documents=["old"], embeddings=[[0.1, 0.2]],
        metadatas=[old_meta or {"artifact_id": "art-1", "valid_from": "2026-10-01", "conversation_id": "conv-full-id"}],
    )
    chroma = FakeChromaClient([col])
    ingest = MagicMock(return_value=ingested if ingested is not None else {"status": "success", "artifact_id": "art-2"})
    supersede = MagicMock(return_value=SimpleNamespace(ok=True, reason=""))
    monkeypatch.setattr(memories, "get_neo4j", lambda: driver)
    monkeypatch.setattr(memories, "get_chroma", lambda: chroma)
    monkeypatch.setattr("app.services.ingestion.ingest_content", ingest)
    monkeypatch.setattr("core.lineage.writer.supersede", supersede)
    monkeypatch.setattr(memories.config, "CERID_MULTI_USER", multi_user)
    monkeypatch.setattr("core.forget.registry.is_forgotten", lambda kind, i: forgotten)
    request = SimpleNamespace(state=SimpleNamespace(is_admin=admin, role="admin" if admin else "user"))
    out = asyncio.run(memories.update_memory("art-1", memories.MemoryUpdateRequest(summary="new"), request))
    return out, SimpleNamespace(queries=session.queries, ingest=ingest, supersede=supersede, chroma=chroma)


def test_an_edited_memory_names_its_type_and_full_conversation(monkeypatch):
    out, _ = _edit(monkeypatch)
    assert out["type"] == "action_item" and out["conversation_id"] == "conv-full-id"


def test_an_edit_is_stored_as_a_new_version_that_supersedes_the_old(monkeypatch):
    """Audit F040: rewriting the Neo4j summary alone left the Chroma document,
    its embedding and the BM25 text on the old wording, so recall kept it."""
    out, seen = _edit(monkeypatch)
    text, domain = seen.ingest.call_args.args
    meta = seen.ingest.call_args.kwargs["metadata"]
    assert (text, domain) == ("new", "conversations")
    assert meta["memory_type"] == "action_item" and meta["conversation_id"] == "conv-full-id"
    assert meta["valid_from"] == "2026-10-01", "an edit corrects the wording, not when it became true"
    _driver, _chroma, old_id, new_id = seen.supersede.call_args.args
    assert (old_id, new_id) == ("art-1", "art-2")
    assert out["id"] == "art-2" and out["content"] == "new"


def test_an_older_version_cannot_be_edited(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _edit(monkeypatch, old_meta={"artifact_id": "art-1", "valid_to": "2026-10-05", "superseded_by": "art-0"})
    assert exc.value.status_code == 409


def test_an_unchanged_edit_keeps_the_version_and_updates_the_summary(monkeypatch):
    out, seen = _edit(monkeypatch, ingested={"status": "duplicate", "artifact_id": "art-1"})
    seen.supersede.assert_not_called()
    assert out["id"] == "art-1"
    assert any("SET a.summary = $summary" in q for q in seen.queries)


def test_only_an_admin_edits_a_memory_in_multi_user_mode(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _edit(monkeypatch, multi_user=True)
    assert exc.value.status_code == 403
    assert _edit(monkeypatch, multi_user=True, admin=True)[0]["content"] == "new"


def test_a_forgotten_memory_cannot_be_edited(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _edit(monkeypatch, forgotten=True)
    assert exc.value.status_code == 404
