# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The positional-to-content-addressed chunk id migration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services import chunk_id_migration as mig
from core.forget.registry import Entry, Registry, Subject
from core.retrieval.chunk_ids import ChunkIdAssigner, is_positional_chunk_id
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection

AID = "a" * 64
HEADER = "Source: notes.md | Domain: general\n\n"


class _Rec(dict):
    def __getitem__(self, key: str) -> Any:
        return dict.get(self, key)


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [_Rec(r) for r in rows]

    def single(self) -> _Rec | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class FakeGraph:
    """Answers the migration's five Cypher shapes from plain dicts."""

    def __init__(self) -> None:
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.mentions: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.corrections: list[dict[str, Any]] = []
        self.fail_on: str | None = None

    def session(self) -> FakeGraph:
        return self

    def __enter__(self) -> FakeGraph:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, query: str, **p: Any) -> _Result:
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("graph down")
        if query.startswith("MATCH (a:Artifact {id: $aid}) RETURN a.chunk_ids"):
            art = self.artifacts.get(p["aid"])
            return _Result([{"ids": art["chunk_ids"]}] if art else [])
        if query.startswith("MATCH (a:Artifact {id: $aid}) SET a.chunk_ids"):
            self.artifacts[p["aid"]]["chunk_ids"] = p["ids"]
            if "chunk_count" in query:
                self.artifacts[p["aid"]]["chunk_count"] = p["n"]
            return _Result([])
        if "RETURN elementId(m)" in query:
            return _Result([{"rid": i, "ids": m["chunk_ids"]} for i, m in enumerate(self.mentions)
                            if m["aid"] == p["aid"]])
        if query.startswith("MATCH ()-[m:MENTIONS]->()"):
            self.mentions[p["rid"]]["chunk_ids"] = p["ids"]
            return _Result([])
        if "WIKILINKS_TO|EMBEDS" in query:
            rows = [r for r in self.links if r["aid"] == p["aid"] and r["source_chunk_id"] in p["olds"]]
            for r in rows:
                r["source_chunk_id"] = p["map"][r["source_chunk_id"]]
            return _Result([{"n": len(rows)}])
        if "Correction" in query:
            rows = [c for c in self.corrections
                    if c["artifact_id"] == p["aid"] and c["applies_to_chunk_id"] in p["olds"]]
            for c in rows:
                c["applies_to_chunk_id"] = p["map"][c["applies_to_chunk_id"]]
            return _Result([{"n": len(rows)}])
        raise AssertionError(f"unexpected query: {query}")


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    reg = Registry(tmp_path / "forget", "m1")
    monkeypatch.setattr("core.forget.registry.get_registry", lambda: reg)
    lexical: dict[str, list[Any]] = {"rekey": [], "remove": []}
    for module in (mig.bm25, mig.sparse_index):
        monkeypatch.setattr(module, "rekey_chunks",
                            lambda d, m, _n=module.__name__: lexical["rekey"].append((_n, d, dict(m))) or len(m))
        monkeypatch.setattr(module, "remove_chunks",
                            lambda d, ids, _n=module.__name__: lexical["remove"].append((_n, d, list(ids))) or len(ids))
    monkeypatch.setattr("app.services.content_lifecycle.invalidate_caches", lambda **kw: None)
    return {"reg": reg, "lexical": lexical, "tmp": tmp_path}


def _row(col: FakeChromaCollection, cid: str, text: str, i: int, level: str = "child", parent: str = "") -> None:
    col.upsert(
        ids=[cid], documents=[text], embeddings=[[float(i + 1), 1.0]],
        metadatas=[{"artifact_id": AID, "domain": "general", "chunk_index": i,
                    "chunk_level": level, "parent_chunk_id": parent}],
    )


def _flat(col: FakeChromaCollection) -> list[str]:
    texts = [HEADER + "first paragraph", HEADER + "second paragraph", HEADER + "first paragraph"]
    for i, t in enumerate(texts):
        _row(col, f"{AID}_chunk_{i}", t, i)
    ids = ChunkIdAssigner(AID)
    return [ids.assign("child", t) for t in texts]


def test_flat_artifact_is_rekeyed_everywhere(env):
    col = FakeChromaCollection("domain_general")
    expected = _flat(col)
    graph = FakeGraph()
    olds = [f"{AID}_chunk_{i}" for i in range(3)]
    graph.artifacts[AID] = {"chunk_ids": json.dumps(olds), "chunk_count": 3}
    graph.mentions.append({"aid": AID, "chunk_ids": json.dumps(olds[:2])})
    graph.links.append({"aid": AID, "source_chunk_id": olds[1]})
    graph.corrections.append({"artifact_id": AID, "applies_to_chunk_id": olds[2]})

    counts = mig.migrate_chunk_ids(chroma=FakeChromaClient([col]), neo4j=graph)

    got = col.get(include=["documents", "embeddings"])
    assert sorted(got["ids"]) == sorted(expected)
    assert len(set(expected)) == 3  # the repeated paragraph gets its own id
    assert list(col.embedding_of(expected[1])) == [2.0, 1.0]
    assert json.loads(graph.artifacts[AID]["chunk_ids"]) == expected
    assert graph.artifacts[AID]["chunk_count"] == 3
    assert json.loads(graph.mentions[0]["chunk_ids"]) == expected[:2]
    assert graph.links[0]["source_chunk_id"] == expected[1]
    assert graph.corrections[0]["applies_to_chunk_id"] == expected[2]
    assert {(n.rsplit(".", 1)[-1], d) for n, d, _ in env["lexical"]["rekey"]} == {("bm25", "general"),
                                                                                   ("sparse_index", "general")}
    assert counts["rows"] == 3 and counts["failed"] == 0


def test_parent_child_links_follow_the_new_ids(env):
    col = FakeChromaCollection("domain_general")
    parent_text = HEADER + "a parent with two children"
    _row(col, f"{AID}_parent_0", parent_text, 0, level="parent")
    _row(col, f"{AID}_child_0_0", "a parent with", 1, parent=f"{AID}_parent_0")
    _row(col, f"{AID}_child_0_1", "two children", 2, parent=f"{AID}_parent_0")
    mig.migrate_chunk_ids(chroma=FakeChromaClient([col]), neo4j=FakeGraph())

    ids = ChunkIdAssigner(AID)
    parent = ids.assign("parent", parent_text)
    child = ids.assign("child", "a parent with")
    assert col.metadata_of(child)["parent_chunk_id"] == parent
    assert col.metadata_of(parent)["parent_chunk_id"] == ""
    assert not any(is_positional_chunk_id(c) for c in col.get(include=[])["ids"])


def test_hype_rows_move_with_their_chunk(env):
    col = FakeChromaCollection("domain_general")
    expected = _flat(col)
    hype = FakeChromaCollection("domain_general_hype")
    hype.upsert(ids=[f"{AID}_chunk_1_hype_0"], documents=["what is second?"], embeddings=[[0.5, 0.5]],
                metadatas=[{"source_chunk_id": f"{AID}_chunk_1", "source_artifact_id": AID}])
    mig.migrate_chunk_ids(chroma=FakeChromaClient([col, hype]), neo4j=FakeGraph())
    assert hype.get(include=[])["ids"] == [f"{expected[1]}_hype_0"]
    assert hype.metadata_of(f"{expected[1]}_hype_0")["source_chunk_id"] == expected[1]


def test_a_second_run_does_nothing(env):
    col = FakeChromaCollection("domain_general")
    _flat(col)
    client = FakeChromaClient([col])
    mig.migrate_chunk_ids(chroma=client, neo4j=FakeGraph())
    before = sorted(col.get(include=[])["ids"])
    counts = mig.migrate_chunk_ids(chroma=client, neo4j=FakeGraph())
    assert counts["artifacts"] == 0 and counts["rows"] == 0
    assert sorted(col.get(include=[])["ids"]) == before


def test_a_crash_after_chroma_is_finished_from_the_recorded_map(env):
    col = FakeChromaCollection("domain_general")
    expected = _flat(col)
    olds = [f"{AID}_chunk_{i}" for i in range(3)]
    graph = FakeGraph()
    graph.artifacts[AID] = {"chunk_ids": json.dumps(olds), "chunk_count": 3}
    graph.fail_on = "RETURN a.chunk_ids"
    client = FakeChromaClient([col])

    first = mig.migrate_chunk_ids(chroma=client, neo4j=graph)
    assert first["failed"] == 1
    assert sorted(col.get(include=[])["ids"]) == sorted(expected)  # Chroma moved; old ids are gone
    assert json.loads(graph.artifacts[AID]["chunk_ids"]) == olds

    graph.fail_on = None
    second = mig.migrate_chunk_ids(chroma=client, neo4j=graph)
    assert second["resumed"] == 1
    assert json.loads(graph.artifacts[AID]["chunk_ids"]) == expected
    assert mig.migrate_chunk_ids(chroma=client, neo4j=graph)["resumed"] == 0


def test_a_purged_chunk_is_deleted_not_rekeyed(env):
    col = FakeChromaCollection("domain_general")
    expected = _flat(col)
    env["reg"].append([Entry("fg_1", Subject("chunk", expected[1]), "purged", "2026-10-09T10:00:00Z", "m2", "ui")])
    graph = FakeGraph()
    graph.artifacts[AID] = {"chunk_ids": json.dumps([f"{AID}_chunk_{i}" for i in range(3)]), "chunk_count": 3}

    counts = mig.migrate_chunk_ids(chroma=FakeChromaClient([col]), neo4j=graph)

    assert sorted(col.get(include=[])["ids"]) == sorted([expected[0], expected[2]])
    assert json.loads(graph.artifacts[AID]["chunk_ids"]) == [expected[0], expected[2]]
    assert graph.artifacts[AID]["chunk_count"] == 2
    removed = [ids for _n, _d, ids in env["lexical"]["remove"]]
    assert removed and all(ids == [f"{AID}_chunk_1"] for ids in removed)
    assert all(f"{AID}_chunk_1" not in m for _n, _d, m in env["lexical"]["rekey"])
    assert counts["dropped_purged"] == 1


def test_a_peer_row_converges_onto_the_migrated_one(env):
    col = FakeChromaCollection("domain_general")
    expected = _flat(col)
    client = FakeChromaClient([col])
    mig.migrate_chunk_ids(chroma=client, neo4j=FakeGraph())
    # An unmigrated machine syncs in its positional copy of the same chunk.
    _row(col, f"{AID}_chunk_1", HEADER + "second paragraph", 1)
    mig.migrate_chunk_ids(chroma=client, neo4j=FakeGraph())
    assert sorted(col.get(include=[])["ids"]) == sorted(expected)


def test_dry_run_changes_nothing(env):
    col = FakeChromaCollection("domain_general")
    _flat(col)
    counts = mig.migrate_chunk_ids(dry_run=True, chroma=FakeChromaClient([col]), neo4j=FakeGraph())
    assert counts["rows"] == 3
    assert all(is_positional_chunk_id(c) for c in col.get(include=[])["ids"])
    assert not (env["tmp"] / "data" / "chunk_id_migration" / "map.jsonl").exists()


def test_rows_of_other_writers_are_left_alone(env):
    col = FakeChromaCollection("domain_conversations")
    col.upsert(ids=["verified_memory_abc"], documents=["fact"], embeddings=[[1.0, 0.0]],
               metadatas=[{"artifact_id": "abc"}])
    counts = mig.migrate_chunk_ids(chroma=FakeChromaClient([col]), neo4j=FakeGraph())
    assert counts["artifacts"] == 0
    assert col.get(include=[])["ids"] == ["verified_memory_abc"]
