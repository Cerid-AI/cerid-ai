# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Derived summaries follow their inputs, against Neo4j (forget phase 5, spec §7).

Probe nodes carry unique ids and are removed afterwards; the queues are
recorded, not run.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.lineage import writer
from tests.helpers.fake_chroma import FakeChromaCollection


@pytest.fixture
def probe(neo4j_driver, monkeypatch):
    tag = f"derivedprobe{uuid.uuid4().hex[:10]}"
    with neo4j_driver.session() as s:
        s.run(
            """
            CREATE (doc:Artifact {id: $tag + '-doc', ingested_at: '2026-01-01T00:00:00Z'})
            CREATE (old:Artifact {id: $tag + '-old', ingested_at: '2026-01-01T00:00:00Z', memory_type: 'preference'})
            CREATE (new:Artifact {id: $tag + '-new', ingested_at: '2026-02-01T00:00:00Z', memory_type: 'preference'})
            CREATE (sum:Artifact {id: $tag + '-sum', memory_scope: 'session_summary'})
            CREATE (c:Conversation {id: $tag + '-conv'})
            CREATE (e:Entity {canonical_id: $tag + '-entity'})
            CREATE (doc)-[:MENTIONS]->(e)
            CREATE (old)-[:EXTRACTED_FROM]->(c), (new)-[:EXTRACTED_FROM]->(c), (sum)-[:EXTRACTED_FROM]->(c)
            CREATE (sum)-[:MENTIONS]->(e)
            CREATE (att:Artifact {id: $tag + '-att'})
            CREATE (e2:Entity {canonical_id: $tag + '-entity2', summary: 'from the attachment'})
            CREATE (doc)-[:HAS_ATTACHMENT]->(att), (att)-[:MENTIONS]->(e2)
            CREATE (:KnowledgeLog {entity_slug: $tag + '-entity2', summary: 'from the attachment'})
            """,
            tag=tag,
        )
    queued: dict[str, list[Any]] = {"pages": [], "trashed": [], "erased": []}
    monkeypatch.setattr("app.deps.get_neo4j", lambda: neo4j_driver)
    monkeypatch.setattr("app.processor.subscribers.wiki_refresh.enqueue_refresh",
                        lambda slug, force=False: queued["pages"].append((slug, force)))
    # the engine is recorded, not run: it would write this machine's registry
    monkeypatch.setattr("app.services.forget.engine.forget_available", lambda: True)
    monkeypatch.setattr("app.services.forget.engine.trash",
                        lambda subjects, requested_by: queued["trashed"].append(([x.id for x in subjects], requested_by)))
    monkeypatch.setattr("app.services.forget.engine.forget_permanently",
                        lambda subjects, requested_by: queued["erased"].append(([x.id for x in subjects], requested_by)))

    def node(nid: str) -> dict[str, Any]:
        with neo4j_driver.session() as s:
            rec = s.run("MATCH (n) WHERE n.id = $id OR n.canonical_id = $id RETURN properties(n) AS p",
                        id=f"{tag}-{nid}").single()
        return dict(rec["p"]) if rec else {}

    yield {"tag": tag, "node": node, "queued": queued, "driver": neo4j_driver}
    with neo4j_driver.session() as s:
        s.run("MATCH (n) WHERE n.id STARTS WITH $tag OR n.canonical_id STARTS WITH $tag DETACH DELETE n", tag=tag)
        s.run("MATCH (k:KnowledgeLog) WHERE k.entity_slug STARTS WITH $tag DELETE k", tag=tag)


def test_a_changed_document_queues_the_pages_written_from_it(probe):
    from app.services.derived import mark_derived_stale

    t = probe["tag"]
    assert mark_derived_stale([f"{t}-doc"]) == {"pages": 2, "summaries": 0}
    assert probe["node"]("entity")["summary_refresh_due"] is True
    assert sorted(probe["queued"]["pages"]) == [(f"{t}-entity", False), (f"{t}-entity2", False)]
    assert probe["node"]("entity2")["summary"] == "from the attachment"  # a new version: the page waits its turn
    assert "summary_stale" not in probe["node"]("sum")


def test_a_forgotten_document_clears_its_pages_and_their_log_at_once(probe):
    """The refresh may skip (no sources left) or wait; the forgotten text must not."""
    from app.services.derived import mark_derived_stale

    t = probe["tag"]
    mark_derived_stale([f"{t}-doc"], forget="erase")
    assert "summary" not in probe["node"]("entity2")  # the attachment's page too
    assert (f"{t}-entity2", True) in probe["queued"]["pages"]
    with probe["driver"].session() as s:
        log = s.run("MATCH (k:KnowledgeLog {entity_slug: $slug}) RETURN k.summary AS summary",
                    slug=f"{t}-entity2").single()
    assert log["summary"] == ""


def test_a_forgotten_memory_takes_its_session_summary_to_the_trash(probe):
    from app.services.derived import mark_derived_stale

    t = probe["tag"]
    assert mark_derived_stale([f"{t}-old"], forget="trash") == {"pages": 0, "summaries": 1}
    assert probe["node"]("sum")["summary_stale"] is True
    assert probe["queued"]["trashed"] == [([f"{t}-sum"], "derived")]
    mark_derived_stale([f"{t}-old"], forget="erase")
    assert probe["queued"]["erased"] == [([f"{t}-sum"], "derived")]


def test_a_failure_while_forgetting_is_raised_so_the_engine_retries(probe, monkeypatch):
    from app.services import derived

    class _Down:
        def session(self) -> Any:
            raise RuntimeError("neo4j down")

    monkeypatch.setattr("app.deps.get_neo4j", lambda: _Down())
    assert derived.mark_derived_stale(["x"]) == {"pages": 0, "summaries": 0}  # a refresh can wait
    with pytest.raises(RuntimeError):
        derived.mark_derived_stale(["x"], forget="erase")


def test_a_summary_is_not_an_input_to_itself(probe):
    from app.services.derived import mark_derived_stale

    t = probe["tag"]
    result = mark_derived_stale([f"{t}-sum"], forget="trash")
    assert result == {"pages": 1, "summaries": 0}  # its page refreshes; it does not retire itself
    assert "summary_stale" not in probe["node"]("sum") and not probe["queued"]["trashed"]


def test_a_supersede_tells_the_app_which_versions_changed(probe):
    from app.services.derived import mark_derived_stale

    t = probe["tag"]
    seen: list[list[str]] = []
    writer.set_on_change(lambda ids: seen.append(ids) or mark_derived_stale(ids))

    class _Client:
        def get_or_create_collection(self, **_kw: Any) -> FakeChromaCollection:
            return FakeChromaCollection("conversations")

    try:
        assert writer.supersede(probe["driver"], _Client(), f"{t}-old", f"{t}-new").ok
        refused = writer.supersede(probe["driver"], _Client(), f"{t}-old", f"{t}-doc")
    finally:
        writer.set_on_change(None)
    assert not refused.ok
    assert seen == [[f"{t}-old", f"{t}-new"]]  # once, and only for the supersede that happened
    assert probe["node"]("sum")["summary_stale"] is True


def test_a_hidden_document_is_no_longer_a_source_of_its_pages(probe):
    from app.db.neo4j.wiki import get_entity

    t = probe["tag"]
    sources = {a["artifact_id"] for a in get_entity(probe["driver"], f"{t}-entity")["source_artifacts"]}
    assert f"{t}-doc" in sources
    with probe["driver"].session() as s:
        s.run("MATCH (a:Artifact {id: $id}) SET a.archived = true", id=f"{t}-doc")
    sources = {a["artifact_id"] for a in get_entity(probe["driver"], f"{t}-entity")["source_artifacts"]}
    assert f"{t}-doc" not in sources


def test_a_trashed_summary_does_not_count_and_a_stale_one_is_refreshed(probe):
    from app.processor.jobs.session_summary import _existing_summaries

    t, d = probe["tag"], probe["driver"]
    assert _existing_summaries(d, f"{t}-conv") == ([f"{t}-sum"], False)
    with d.session() as s:
        s.run("MATCH (a:Artifact {id: $id}) SET a.summary_stale = true", id=f"{t}-sum")
    assert _existing_summaries(d, f"{t}-conv") == ([f"{t}-sum"], True)
    with d.session() as s:
        s.run("MATCH (a:Artifact {id: $id}) SET a.archived = true", id=f"{t}-sum")
    assert _existing_summaries(d, f"{t}-conv") == ([], False)


def test_an_older_memory_export_never_reopens_a_newer_version(probe, tmp_path):
    import json

    from app.sync import import_ as import_mod

    t, d = probe["tag"], probe["driver"]
    mid = f"{t}-mem"
    with d.session() as s:
        s.run("CREATE (:Memory {id: $id, lineage_id: $id, valid_to: '2026-10-05', superseded_by: 'x', "
              "updated_at: '2026-10-05T00:00:00+00:00'})", id=mid)
    (tmp_path / "neo4j").mkdir()
    rows = [{"id": mid, "props": {"id": mid, "lineage_id": mid, "updated_at": "2026-10-01T00:00:00+00:00"}}]
    (tmp_path / "neo4j" / "memories.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    import_mod.import_memories(d, sync_dir=str(tmp_path))
    assert probe["node"]("mem")["valid_to"] == "2026-10-05"  # the older export lost

    rows[0]["props"]["updated_at"] = "2026-10-09T00:00:00+00:00"
    (tmp_path / "neo4j" / "memories.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    import_mod.import_memories(d, sync_dir=str(tmp_path))
    node = probe["node"]("mem")
    assert "valid_to" not in node and "superseded_by" not in node  # the newer one reopened it
