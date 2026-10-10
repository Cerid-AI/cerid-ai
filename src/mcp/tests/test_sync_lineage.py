# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Sync carries versions (forget phase 5, spec §7): which version is current,
the passages a version closed, and the facts each version holds, so two
machines agree on what is true now and what was true before."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import app.sync.import_ as import_mod
from app.sync._helpers import CHROMA_SUBDIR, FACTS_JSONL, LINEAGE_PROPS, NEO4J_SUBDIR


def _cypher_calls(session: MagicMock) -> list[tuple[str, dict[str, Any]]]:
    return [(str(c.args[0]), dict(c.kwargs)) for c in session.run.call_args_list]


# ---- artifact lineage ----

def test_a_reopened_version_loses_its_closing_here_too():
    session = MagicMock()
    import_mod._apply_lineage(session, "a1", {
        "lineage_id": "a0", "version": 2, "valid_to": None, "superseded_by": None, "memory_scope": "user",
    })
    (_, kwargs), = _cypher_calls(session)
    props = kwargs["props"]
    assert set(LINEAGE_PROPS) <= set(props)
    assert props["valid_to"] is None and props["superseded_by"] is None  # null clears under SET +=
    assert props["lineage_id"] == "a0" and props["version"] == 2 and props["memory_scope"] == "user"


def test_a_node_without_lineage_leaves_this_machine_s_alone():
    session = MagicMock()
    import_mod._apply_lineage(session, "a1", {"lineage_id": None, "valid_to": None, "conversation_id": "c1"})
    (_, kwargs), = _cypher_calls(session)
    assert kwargs["props"] == {"conversation_id": "c1"}

    session = MagicMock()
    import_mod._apply_lineage(session, "a1", {"lineage_id": None, "valid_to": None})
    session.run.assert_not_called()


def test_the_source_path_is_never_synced():
    """It names a file on one machine; the other machine matches its own."""
    from app.sync._helpers import IDENTITY_PROPS
    from app.sync.export import _LINEAGE_COLUMNS

    assert "source_path" not in LINEAGE_PROPS + IDENTITY_PROPS
    assert "source_path" not in _LINEAGE_COLUMNS


def test_a_memory_with_lineage_clears_the_fields_it_lacks():
    props = import_mod._with_lineage_cleared({"id": "m2", "lineage_id": "m1", "version": 2})
    assert props["valid_to"] is None and props["superseded_by"] is None and props["version"] == 2
    plain = {"id": "m3", "text": "no history"}
    assert import_mod._with_lineage_cleared(plain) == plain


# ---- passages ----

class _Chroma:
    """A chromadb 1.x server holding rows this machine already has."""

    def __init__(self, held: dict[str, dict[str, Any]]):
        self.held = held
        self.updates: list[dict[str, Any]] = []
        self.added: list[str] = []

    def get(self, url: str, **_kw: Any) -> Any:
        return SimpleNamespace(status_code=200, json=lambda: {"id": "coll-1"}, raise_for_status=lambda: None)

    def post(self, url: str, **kw: Any) -> Any:
        body = kw.get("json") or {}
        payload: dict[str, Any] = {}
        if url.endswith("/get"):
            payload = {"ids": [] if body.get("offset") else list(self.held)}
        elif url.endswith("/update"):
            self.updates.append(json.loads(json.dumps(body)))
        elif url.endswith("/add"):
            self.added.extend(body.get("ids") or [])
        return SimpleNamespace(status_code=200, json=lambda: payload, raise_for_status=lambda: None)


def _write_chroma_export(sync_dir, rows: list[dict[str, Any]]) -> None:
    out = sync_dir / CHROMA_SUBDIR
    out.mkdir(parents=True)
    (out / "domain_general.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_rows_of_an_artifact_taken_from_the_other_machine_take_its_version_state(tmp_path, monkeypatch):
    monkeypatch.setattr(import_mod.config, "DOMAINS", ["general"], raising=False)
    server = _Chroma({"a1_chunk_0": {}, "a1_chunk_1": {}, "a1_chunk_2": {}, "b1_chunk_0": {}, "x9_chunk_0": {}})
    monkeypatch.setattr(import_mod, "httpx", server)
    closed = {"artifact_id": "a1", "version": 1, "valid_to": "2026-10-01T00:00:00+00:00", "version_closed": 1,
              "tenant_id": "other", "parent_chunk_id": "elsewhere"}
    _write_chroma_export(tmp_path, [
        {"id": "a1_chunk_0", "document": "old text", "metadata": closed, "embedding": [0.1]},
        {"id": "a1_chunk_1", "document": "kept", "metadata": {"artifact_id": "a1", "version_closed": 0, "valid_to": ""},
         "embedding": [0.2]},
        {"id": "b1_chunk_0", "document": "other", "metadata": {"artifact_id": "b1", "version_closed": 1}, "embedding": [0.3]},
        # a row of another artifact relabelled as a1 is not a1's to change
        {"id": "x9_chunk_0", "document": "x", "metadata": {"artifact_id": "a1", "version_closed": 0}, "embedding": [0.4]},
        # a passage forgotten here is not reopened by the other machine
        {"id": "a1_chunk_2", "document": "y", "metadata": {"artifact_id": "a1", "version_closed": 0, "valid_to": ""},
         "embedding": [0.5]},
    ])
    monkeypatch.setattr(import_mod, "_is_forgotten_chunk", lambda cid, meta: cid == "a1_chunk_2")

    result = import_mod.import_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path), update_artifacts={"a1"},
    )

    (update,) = server.updates
    assert update["ids"] == ["a1_chunk_0", "a1_chunk_1"]
    first, second = update["metadatas"]
    assert first == {"version": 1, "valid_to": "2026-10-01T00:00:00+00:00", "version_closed": 1}
    assert second == {"valid_to": "", "version_closed": 0}  # an explicit "" reopens
    assert "documents" not in update and "embeddings" not in update  # content-addressed: text never moves
    assert result["domains"]["general"] == {"added": 0, "skipped": 3, "updated": 2}
    assert result["total_updated"] == 2 and not server.added


def test_without_artifacts_taken_rows_already_held_are_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(import_mod.config, "DOMAINS", ["general"], raising=False)
    server = _Chroma({"p1": {}})
    monkeypatch.setattr(import_mod, "httpx", server)
    _write_chroma_export(tmp_path, [
        {"id": "p1", "document": "x", "metadata": {"artifact_id": "a1", "version_closed": 1}, "embedding": [0.1]},
    ])
    result = import_mod.import_chroma(chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path))
    assert not server.updates and result["total_updated"] == 0


# ---- facts ----

def _write_facts(sync_dir, rows: list[dict[str, Any]]) -> None:
    out = sync_dir / NEO4J_SUBDIR
    out.mkdir(parents=True)
    (out / FACTS_JSONL).write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _fact(uid: str, source: str, **extra: Any) -> dict[str, Any]:
    return {"props": {"uid": uid, "source_artifact_id": source, "fact_key": "city", "value": "Denver", **extra},
            "subjects": ["person:me"], "objects": []}


def test_a_fact_goes_only_where_its_memory_is(tmp_path, monkeypatch):
    _write_facts(tmp_path, [
        _fact("person:me|city|m1", "m1", valid_to="2026-10-01T00:00:00+00:00", closed_by="m2", archived=True),
        _fact("person:me|city|gone", "gone"),
        _fact("person:me|city|absent", "absent"),
        {"props": {"uid": "person:me|city|", "fact_key": "city"}, "subjects": [], "objects": []},
        _fact("person:me|city|m3", "m3"),            # reopened there: its closure is cleared here
        _fact("person:me|city|local", "local"),      # this machine's version is newer: left alone
    ])
    monkeypatch.setattr(import_mod, "_is_forgotten_artifact", lambda aid: aid == "gone")
    session = MagicMock()

    def _run(cypher: str, **kw: Any) -> Any:
        found = 1 if kw.get("id") in ("m1", "m3", "local") else 0
        return SimpleNamespace(single=lambda: {"n": found})

    session.run.side_effect = _run
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session

    result = import_mod.import_facts(driver, sync_dir=str(tmp_path), applied={"m1", "m3", "gone", "absent"})

    assert result == {"facts_merged": 2, "facts_skipped": 4}
    merges = [kw for cypher, kw in _cypher_calls(session) if "MERGE (f:Fact" in cypher]
    merged, reopened = merges
    assert reopened["uid"] == "person:me|city|m3"
    assert reopened["props"]["valid_to"] is None and reopened["props"]["closed_by"] is None
    assert merged["uid"] == "person:me|city|m1" and merged["source"] == "m1"
    assert merged["props"]["closed_by"] == "m2" and "uid" not in merged["props"]
    assert "archived" not in merged["props"]  # only the properties a fact has
    assert merged["subjects"] == ["person:me"]


def test_facts_export_carries_every_version(tmp_path):
    from app.sync.export import export_facts

    session = MagicMock()
    session.run.return_value = iter([
        {"props": {"uid": "s|k|m1", "source_artifact_id": "m1", "valid_to": "2026-10-01"}, "subjects": ["s"], "objects": []},
        {"props": {"uid": "s|k|m2", "source_artifact_id": "m2"}, "subjects": ["s"], "objects": ["o"]},
    ])
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session

    assert export_facts(driver, sync_dir=str(tmp_path)) == {"facts": 2}
    rows = [json.loads(line) for line in (tmp_path / NEO4J_SUBDIR / FACTS_JSONL).read_text().splitlines()]
    assert [r["props"]["uid"] for r in rows] == ["s|k|m1", "s|k|m2"]
    assert "WHERE" not in session.run.call_args.args[0]  # closed facts travel too


# ---- keyword hits closed elsewhere ----

@pytest.mark.asyncio
async def test_a_keyword_hit_on_a_passage_closed_by_a_sync_is_not_a_current_answer(monkeypatch):
    from core.agents import query_agent
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setenv("CERID_FILTER_PENDING_CHUNKS", "false")
    monkeypatch.setattr(bm25_mod, "is_available", lambda: True)
    monkeypatch.setattr(bm25_mod, "search_bm25", lambda *a, **k: [("closed", 9.0), ("open", 8.0)])
    monkeypatch.setattr(query_agent, "_parent_child_enabled", lambda: False)
    monkeypatch.setattr(query_agent, "_unsearchable_folder_ids", lambda: set())
    metas = {
        "closed": {"artifact_id": "a1", "domain": "general", "version_closed": 1, "valid_to": "2026-10-01T00:00:00+00:00"},
        "open": {"artifact_id": "a1", "domain": "general", "version_closed": 0},
    }

    def _get(ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {"ids": ids, "documents": [f"text of {i}" for i in ids], "metadatas": [metas[i] for i in ids]}

    collection = SimpleNamespace(
        query=lambda **kw: {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]},
        get=_get, count=lambda: 2,
    )
    chroma = SimpleNamespace(
        list_collections=lambda: [SimpleNamespace(name=query_agent.config.collection_name("general"))],
        get_collection=lambda name: collection,
    )

    current = await query_agent.multi_domain_query(query="city", domains=["general"], top_k=5, chroma_client=chroma)
    assert [r["chunk_id"] for r in current] == ["open"]

    past = await query_agent.multi_domain_query(
        query="city", domains=["general"], top_k=5, chroma_client=chroma, as_of="2026-09-01",
    )
    assert "closed" in [r["chunk_id"] for r in past]


# ---- wiki pages stay on their machine ----

def test_a_wiki_page_never_travels(tmp_path):
    from app.sync.export import export_entities

    session = MagicMock()
    session.run.side_effect = [
        iter([{"canonical_id": "e1", "props": {
            "canonical_id": "e1", "name": "Denver", "summary": "forgotten text", "summary_updated_at": "t",
            "summary_edited_by": "me", "external_references": "[]", "mention_count": 3,
        }}]),
        iter([]),
    ]
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    export_entities(driver, sync_dir=str(tmp_path))
    row = json.loads((tmp_path / NEO4J_SUBDIR / "entities.jsonl").read_text().splitlines()[0])
    assert row["props"] == {"canonical_id": "e1", "name": "Denver", "mention_count": 3}

    # and an older export that still carries one is not applied
    session = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    (tmp_path / NEO4J_SUBDIR / "entities.jsonl").write_text(json.dumps(
        {"canonical_id": "e1", "props": {"name": "Denver", "summary": "forgotten text"}}) + "\n")
    import_mod.import_entities(driver, sync_dir=str(tmp_path))
    merges = [kw for cypher, kw in _cypher_calls(session) if "MERGE (e:Entity" in cypher]
    assert merges and all("summary" not in kw["props"] for kw in merges)


def test_an_older_memory_export_never_reopens_a_newer_version_here(tmp_path):
    out = tmp_path / NEO4J_SUBDIR
    out.mkdir(parents=True)
    (out / "memories.jsonl").write_text(json.dumps(
        {"id": "m1", "props": {"id": "m1", "lineage_id": "m1", "version": 1, "updated_at": "2026-10-01"}}) + "\n")
    session = MagicMock()
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    import_mod.import_memories(driver, sync_dir=str(tmp_path))
    cypher, kwargs = next((c, k) for c, k in _cypher_calls(session) if "MERGE (m:Memory" in c)
    assert "$updated >= m.updated_at" in cypher and kwargs["updated"] == "2026-10-01"
    assert kwargs["props"]["valid_to"] is None  # cleared only where the newer side wins
