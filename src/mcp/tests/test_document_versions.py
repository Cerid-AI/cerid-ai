# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Document versions (forget phase 5, PR C): retention that never overrides a
person's forget, the version record, source-path identity, undo, and history
for document passages."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.forget.registry import RETENTION, Entry, Subject
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection
from tests.helpers.forget import isolate_forget

A = "a" * 64


@pytest.fixture
def reg(tmp_path: Path, monkeypatch):
    return isolate_forget(monkeypatch, tmp_path)


def _purge(reg, cid: str, fid: str, by: str) -> None:
    reg.append([Entry(fid, Subject("chunk", cid), "purged", "2026-10-01T00:00:00Z", "m1", by)])


# ---- retention and the ingest barrier ----

def test_a_passage_retention_erased_may_come_back_but_a_person_s_forget_stands(reg):
    from app.services.ingestion import _drop_purged_chunks

    _purge(reg, "old_passage", "fg_r", RETENTION)
    _purge(reg, "forgotten_by_me", "fg_p", "ui")
    _purge(reg, "both", "fg_r2", RETENTION)
    _purge(reg, "both", "fg_p2", "ui")
    records = [{"id": i, "parent_id": ""} for i in ("old_passage", "forgotten_by_me", "both", "new")]
    kept, _ = _drop_purged_chunks(records, [])
    assert [r["id"] for r in kept] == ["old_passage", "new"]
    assert reg.state_of("chunk", "old_passage") == "readded"
    assert reg.state_of("chunk", "both") == "purged"  # the person's forget still holds


def test_record_readd_can_cancel_only_retention_forgets(reg):
    _purge(reg, "both", "fg_r", RETENTION)
    _purge(reg, "both", "fg_p", "ui")
    assert reg.forgotten_only_by("chunk", RETENTION) == frozenset()
    assert reg.record_readd("chunk", "both", requested_by="ingest", only_requested_by=RETENTION)
    assert reg.is_forgotten("chunk", "both")


# ---- the version record and retention ----

def test_each_new_version_is_recorded_and_old_ones_retire(monkeypatch):
    from app.services import ingestion

    monkeypatch.setattr("config.DOCUMENT_VERSIONS_KEPT", 2)
    retired: list[str] = []
    monkeypatch.setattr(ingestion, "_retire_versions", lambda aid, cutoff: retired.append(cutoff) or 1)
    monkeypatch.setattr(ingestion, "get_neo4j", lambda: object())
    written: dict[str, Any] = {}
    with patch("app.services.ingestion.graph") as graph:
        graph.set_artifact_properties.side_effect = lambda d, aid, props: written.update(props)
        prev = {"version": 2, "content_hash": "h2", "ingested_at": "2026-01-01",
                "versions": '[{"version": 1, "content_hash": "h1", "valid_from": "2026-01-01"},'
                            ' {"version": 2, "content_hash": "h2", "valid_from": "2026-02-01"}]'}
        ingestion._record_document_version(prev, A, 3, "h3", "2026-03-01")
    assert retired == ["2026-02-01"]  # the start of the oldest version kept
    assert written["version"] == 3 and written["lineage_id"] == A
    import json
    assert [v["version"] for v in json.loads(written["versions"])] == [2, 3]


def test_retiring_erases_only_passages_closed_before_the_oldest_kept_version(monkeypatch):
    from app.services import ingestion

    col = FakeChromaCollection("domain_general")
    rows = [("gone", {"version_closed": 1, "valid_to": "2026-01-15"}),
            ("kept_history", {"version_closed": 1, "valid_to": "2026-02-15"}),
            ("current", {"version_closed": 0, "valid_to": ""})]
    for cid, meta in rows:
        col.upsert(ids=[cid], documents=[cid], embeddings=[[1.0, 0.0]], metadatas=[{"artifact_id": A, **meta}])
    monkeypatch.setattr(ingestion, "get_chroma", lambda: FakeChromaClient([col]))
    monkeypatch.setattr(ingestion, "get_neo4j", lambda: object())
    monkeypatch.setattr("config.collection_name", lambda domain: "domain_general")
    forgotten: list[Any] = []
    with patch("app.services.ingestion.graph") as graph, \
         patch("app.services.forget.engine.forget_available", return_value=True), \
         patch("app.services.forget.engine.forget_permanently",
               side_effect=lambda subjects, requested_by: forgotten.append((subjects, requested_by))):
        graph.get_artifact_versions.return_value = {"domain": "general"}
        assert ingestion._retire_versions(A, "2026-02-01") == 1
    (subjects, by), = forgotten
    assert [s.id for s in subjects] == ["gone"] and by == RETENTION


# ---- source identity ----

@pytest.mark.parametrize("known,exists,expected", [
    ("", False, {"source_path": "/new/report.pdf"}),                               # adopts a path
    ("/old/report.pdf", False, {"source_path": "/new/report.pdf", "filename": "report.pdf",
                                "updated_at": "T"}),                               # moved, and synced
    ("/old/report.pdf", True, None),                                               # a copy: nothing changes
    ("/new/report.pdf", False, None),                                              # already known
])
def test_identical_content_from_a_path_adopts_or_moves_it(monkeypatch, known, exists, expected):
    from app.services import ingestion

    monkeypatch.setattr(ingestion, "get_neo4j", lambda: object())
    monkeypatch.setattr("os.path.exists", lambda p: exists)
    monkeypatch.setattr("os.path.isdir", lambda p: True)  # the old folder is mounted
    monkeypatch.setattr(ingestion, "utcnow_iso", lambda: "T")
    with patch("app.services.ingestion.graph") as graph:
        graph.get_artifact_versions.return_value = {"source_path": known}
        ingestion._note_source_path(A, "/new/report.pdf", "report.pdf")
    if expected is None:
        graph.set_artifact_properties.assert_not_called()
    else:
        graph.set_artifact_properties.assert_called_once_with(graph.set_artifact_properties.call_args.args[0],
                                                              A, expected)


def test_the_basename_match_is_only_among_documents_with_no_path():
    from app.db.neo4j.artifacts import find_artifact_by_filename, find_artifact_by_source_path

    session = MagicMock()
    session.run.return_value.single.return_value = None
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    find_artifact_by_filename(driver, "report.pdf", "general")
    assert "a.source_path IS NULL" in session.run.call_args.args[0]
    find_artifact_by_source_path(driver, "/a/report.pdf", "general")
    assert "{source_path: $source_path, domain: $domain}" in session.run.call_args.args[0]
    assert find_artifact_by_source_path(driver, "", "general") is None


# ---- undo this update ----

def test_undoing_a_document_update_reopens_the_previous_passages_and_trashes_the_new(monkeypatch):
    from app.services import lineage_undo

    col = FakeChromaCollection("domain_general")
    started = "2026-03-01T00:00:00+00:00"
    for cid, meta in [
        ("kept", {"valid_from": "2026-01-01", "version_closed": 0, "valid_to": "", "version": 2}),
        ("new", {"valid_from": started, "version_closed": 0, "valid_to": "", "version": 2}),
        ("old", {"valid_from": "2026-01-01", "version_closed": 1, "valid_to": started, "version": 1}),
        ("older", {"valid_from": "2025-01-01", "version_closed": 1, "valid_to": "2026-01-01", "version": 1}),
    ]:
        col.upsert(ids=[cid], documents=[f"text of {cid}"], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": A, "chunk_level": "child", **meta}])
    monkeypatch.setattr(lineage_undo, "get_chroma", lambda: FakeChromaClient([col]))
    monkeypatch.setattr(lineage_undo, "get_neo4j", lambda: object())
    monkeypatch.setattr("config.collection_name", lambda domain: "domain_general")
    monkeypatch.setattr(lineage_undo, "_reindex", lambda *a: None)
    monkeypatch.setattr(lineage_undo, "_after_content_change", lambda *a: None)
    trashed: list[Any] = []
    written: dict[str, Any] = {}
    with patch("app.services.lineage_undo.graph") as graph, \
         patch("app.services.forget.engine.trash",
               side_effect=lambda subjects, requested_by: trashed.append((subjects, requested_by)) or "fg_u"):
        graph.get_artifact_versions.return_value = {"domain": "general", "content_hash": "h2", "versions": (
            '[{"version": 1, "content_hash": "h1", "valid_from": "2026-01-01"},'
            ' {"version": 2, "content_hash": "h2", "valid_from": "' + started + '"}]')}
        graph.set_artifact_properties.side_effect = lambda d, aid, props: written.update(props)
        result = lineage_undo._undo_document(A)
    assert result["version"] == 1 and result["reopened"] == 1 and result["trashed"] == 1
    assert col.metadata_of("old")["version_closed"] == 0 and col.metadata_of("old")["valid_to"] == ""
    assert col.metadata_of("new")["version_closed"] == 1
    assert col.metadata_of("older")["version_closed"] == 1  # history before that stays history
    (subjects, by), = trashed
    assert [s.id for s in subjects] == ["new"] and by == lineage_undo.UNDO
    assert written["version"] == 1 and written["content_hash"] == "h1"
    assert sorted(__import__("json").loads(written["chunk_ids"])) == ["kept", "old"]


def test_undo_refuses_when_the_earlier_version_was_forgotten(monkeypatch, reg):
    from app.services import lineage_undo

    col = FakeChromaCollection("domain_general")
    started = "2026-03-01T00:00:00+00:00"
    for cid, meta in [
        ("new", {"valid_from": started, "version_closed": 0, "valid_to": "", "version": 2}),
        ("old", {"valid_from": "2026-01-01", "version_closed": 1, "valid_to": started, "version": 1}),
    ]:
        col.upsert(ids=[cid], documents=[f"text of {cid}"], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": A, "chunk_level": "child", **meta}])
    reg.append([Entry("fg_e", Subject("chunk", "old"), "trashed", "2026-10-01T00:00:00Z", "m1", "ui")])
    monkeypatch.setattr(lineage_undo, "get_chroma", lambda: FakeChromaClient([col]))
    monkeypatch.setattr(lineage_undo, "get_neo4j", lambda: object())
    monkeypatch.setattr("config.collection_name", lambda domain: "domain_general")
    reindex = MagicMock()
    monkeypatch.setattr(lineage_undo, "_reindex", reindex)
    with patch("app.services.lineage_undo.graph") as graph:
        graph.get_artifact_versions.return_value = {"domain": "general", "versions": (
            '[{"version": 1, "valid_from": "2026-01-01"}, {"version": 2, "valid_from": "' + started + '"}]')}
        with pytest.raises(lineage_undo.NothingToUndo):
            lineage_undo._undo_document(A)
    assert col.metadata_of("old")["version_closed"] == 1 and col.metadata_of("new")["version_closed"] == 0
    reindex.assert_not_called()
    graph.set_artifact_properties.assert_not_called()


def test_a_document_with_one_version_has_nothing_to_undo(monkeypatch):
    from app.services import lineage_undo

    monkeypatch.setattr(lineage_undo, "get_neo4j", lambda: object())
    with patch("app.services.lineage_undo.graph") as graph:
        graph.get_artifact_versions.return_value = {"domain": "general", "versions": "[]"}
        with pytest.raises(lineage_undo.NothingToUndo):
            lineage_undo._undo_document(A)


# ---- history for a document passage ----

def test_a_document_passage_s_history_is_what_held_its_place_before(monkeypatch):
    from core.lineage.history import attach_history

    monkeypatch.setattr("core.lineage.history.forgotten_ids", lambda kind: frozenset())
    col = FakeChromaCollection("domain_general")
    for cid, index, version, closed, text in [
        ("p0_v3", 0, 3, 0, "Office is on the 9th floor"),
        ("p0_v2", 0, 2, 1, "Office is on the 5th floor"),
        ("p1_v1", 1, 1, 1, "Parking is in lot B"),
    ]:
        col.upsert(ids=[cid], documents=[text], embeddings=[[1.0, 0.0]], metadatas=[{
            "artifact_id": A, "lineage_id": A, "version": version, "chunk_index": index,
            "chunk_level": "child", "version_closed": closed, "valid_to": "2026-05-01" if closed else "",
        }])

    class Graph:
        def session(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def run(self, *_a, **_k):
            return []

    row = {"artifact_id": A, "chunk_id": "p0_v3", "collection": "domain_general", "lineage_id": A,
           "version": 3, "chunk_index": 0, "chunk_level": "child"}
    attach_history([row], FakeChromaClient([col]), Graph())
    assert [h["value"] for h in row["history"]] == ["Office is on the 5th floor"]


# ---- review fixes ----

def test_a_passage_an_undo_trashed_comes_back_with_its_text(reg):
    from app.services.ingestion import _drop_purged_chunks
    from core.forget.registry import UNDO

    reg.append([Entry("fg_u", Subject("chunk", "v2_only"), "trashed", "2026-10-01T00:00:00Z", "m1", UNDO)])
    reg.append([Entry("fg_p", Subject("chunk", "mine"), "trashed", "2026-10-01T00:00:00Z", "m1", "ui")])
    _drop_purged_chunks([{"id": "v2_only", "parent_id": ""}, {"id": "mine", "parent_id": ""}], [])
    assert not reg.is_forgotten("chunk", "v2_only")  # the file still says it: the undo gives way
    assert reg.is_forgotten("chunk", "mine")          # a person's forget never does


def test_a_caller_without_a_path_still_finds_the_document_of_that_name():
    from app.db.neo4j.artifacts import find_artifact_by_filename

    session = MagicMock()
    session.run.return_value.single.return_value = None
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    find_artifact_by_filename(driver, "report.pdf", "general", any_path=True)
    assert "source_path IS NULL" not in session.run.call_args.args[0]


def test_an_unmounted_folder_is_not_a_move(monkeypatch):
    from app.services import ingestion

    monkeypatch.setattr(ingestion, "get_neo4j", lambda: object())
    monkeypatch.setattr("os.path.exists", lambda p: False)
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    with patch("app.services.ingestion.graph") as graph:
        graph.get_artifact_versions.return_value = {"source_path": "/Volumes/offline/report.pdf"}
        ingestion._note_source_path(A, "/elsewhere/report.pdf", "report.pdf")
    graph.set_artifact_properties.assert_not_called()


def test_extraction_reads_a_document_as_it_stands_and_a_superseded_memory_whole():
    from app.processor.jobs.entity_extraction import EntityExtractionJob

    col = FakeChromaCollection("domain_general")
    for cid, aid, closed in [("now", A, 0), ("before", A, 1), ("old_memory", "m" * 64, 1)]:
        col.upsert(ids=[cid], documents=[cid], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": aid, "version_closed": closed, "valid_to": "x" if closed else ""}])
    client = FakeChromaClient([col])
    assert EntityExtractionJob._fetch_chunks(client, "domain_general", A)[0] == ["now"]
    assert EntityExtractionJob._fetch_chunks(client, "domain_general", "m" * 64)[0] == ["old_memory"]


def test_forgetting_earlier_versions_takes_only_the_closed_passages(monkeypatch):
    from app.services import lineage_undo

    col = FakeChromaCollection("domain_general")
    for cid, closed in [("now", 0), ("then_1", 1), ("then_2", 1)]:
        col.upsert(ids=[cid], documents=[cid], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": A, "version_closed": closed}])
    monkeypatch.setattr(lineage_undo, "get_chroma", lambda: FakeChromaClient([col]))
    monkeypatch.setattr(lineage_undo, "get_neo4j", lambda: object())
    monkeypatch.setattr("config.collection_name", lambda domain: "domain_general")
    trashed: list[Any] = []
    with patch("app.services.lineage_undo.graph") as graph, \
         patch("app.services.forget.engine.forget_available", return_value=True), \
         patch("app.services.forget.engine.trash",
               side_effect=lambda subjects, requested_by, user_id: trashed.append(subjects) or "fg_e"):
        graph.get_artifact_versions.return_value = {"domain": "general"}
        result = lineage_undo.forget_earlier_versions(A, "trash")
    assert result == {"forget_id": "fg_e", "state": "trashed", "passages": 2}
    assert sorted(s.id for s in trashed[0]) == ["then_1", "then_2"]
