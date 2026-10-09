# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Forgetting a passage: the chunk adapter, the ingest barrier and the sync skips."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.forget.adapters import ChunkAdapter
from core.forget.registry import Entry, Subject
from core.retrieval.chunk_ids import ChunkIdAssigner
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection
from tests.helpers.forget import isolate_forget

AID = "a" * 64
OTHER = "b" * 64


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


class Graph:
    def __init__(self, chunk_ids: list[str], mentions: list[list[str]]) -> None:
        self.node = {"domain": "general", "chunk_ids": json.dumps(chunk_ids), "chunk_count": len(chunk_ids)}
        self.mentions = [json.dumps(m) for m in mentions]

    def session(self) -> Graph:
        return self

    def __enter__(self) -> Graph:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, query: str, **p: Any) -> _Result:
        if "RETURN a.domain AS domain, a.chunk_ids AS ids" in query:
            return _Result([{"domain": self.node["domain"], "ids": self.node["chunk_ids"]}] if p["aid"] == AID else [])
        if "SET a.chunk_ids" in query:
            self.node.update(chunk_ids=p["ids"], chunk_count=p["n"])
            return _Result([])
        if "RETURN elementId(m)" in query:
            return _Result([{"rid": i, "ids": m} for i, m in enumerate(self.mentions)])
        if "SET m.chunk_ids" in query:
            self.mentions[p["rid"]] = p["ids"]
            return _Result([])
        raise AssertionError(query)


@pytest.fixture
def stores(tmp_path: Path, monkeypatch):
    reg = isolate_forget(monkeypatch, tmp_path)
    col = FakeChromaCollection("domain_general")
    hype = FakeChromaCollection("domain_general_hype")
    ids = ChunkIdAssigner(AID)
    parent = ids.assign("parent", "parent text one two")
    child1 = ids.assign("child", "parent text")
    child2 = ids.assign("child", "one two")
    flat = ids.assign("child", "a flat passage")
    rows = [(parent, "parent", ""), (child1, "child", parent), (child2, "child", parent), (flat, "child", "")]
    for i, (cid, level, par) in enumerate(rows):
        col.upsert(ids=[cid], documents=[cid], embeddings=[[1.0, float(i)]],
                   metadatas=[{"artifact_id": AID, "domain": "general", "chunk_level": level,
                               "parent_chunk_id": par, "chunk_index": i}])
    for cid in (child1, flat):
        hype.upsert(ids=[f"{cid}_hype_0"], documents=["q"], embeddings=[[0.0, 1.0]],
                    metadatas=[{"source_chunk_id": cid, "source_artifact_id": AID}])
    client = FakeChromaClient([col, hype])
    graph = Graph([child1, child2, flat], [[child1, flat], [child2]])
    lexical: list[tuple[str, list[str]]] = []
    monkeypatch.setattr("app.deps.get_chroma", lambda: client)
    monkeypatch.setattr("app.deps.get_neo4j", lambda: graph)
    monkeypatch.setattr("app.services.content_lifecycle.bm25.remove_chunks",
                        lambda d, c: lexical.append(("bm25", list(c))) or len(c))
    monkeypatch.setattr("app.services.content_lifecycle.sparse_index.remove_chunks",
                        lambda d, c: lexical.append(("sparse", list(c))) or len(c))
    monkeypatch.setattr("app.services.content_lifecycle.invalidate_caches", lambda **kw: None)
    return {"reg": reg, "col": col, "hype": hype, "graph": graph, "lexical": lexical,
            "parent": parent, "child1": child1, "child2": child2, "flat": flat}


def test_purging_a_flat_passage_removes_it_everywhere(stores):
    s = stores
    result = ChunkAdapter().purge(Subject("chunk", s["flat"]))
    assert result.removed == 1
    assert s["flat"] not in s["col"].get(include=[])["ids"]
    assert s["hype"].get(include=[])["ids"] == [f"{s['child1']}_hype_0"]
    assert {(n, tuple(c)) for n, c in s["lexical"]} == {("bm25", (s["flat"],)), ("sparse", (s["flat"],))}
    assert json.loads(s["graph"].node["chunk_ids"]) == [s["child1"], s["child2"]]
    assert s["graph"].node["chunk_count"] == 2
    assert [json.loads(m) for m in s["graph"].mentions] == [[s["child1"]], [s["child2"]]]


def test_purging_a_parent_takes_its_children_and_their_questions(stores):
    s = stores
    result = ChunkAdapter().purge(Subject("chunk", s["parent"]))
    assert result.removed == 3
    assert s["col"].get(include=[])["ids"] == [s["flat"]]
    assert s["hype"].get(include=[])["ids"] == [f"{s['flat']}_hype_0"]
    assert json.loads(s["graph"].node["chunk_ids"]) == [s["flat"]]
    assert [json.loads(m) for m in s["graph"].mentions] == [[s["flat"]], []]


def test_purge_is_idempotent(stores):
    s = stores
    ChunkAdapter().purge(Subject("chunk", s["flat"]))
    again = ChunkAdapter().purge(Subject("chunk", s["flat"]))
    assert again.removed == 0
    assert json.loads(s["graph"].node["chunk_ids"]) == [s["child1"], s["child2"]]


def test_a_passage_of_another_artifact_is_never_touched(stores):
    s = stores
    s["col"].upsert(ids=[f"{OTHER}_0123456789abcdef"], documents=["x"], embeddings=[[1.0, 1.0]],
                    metadatas=[{"artifact_id": OTHER, "parent_chunk_id": s["parent"]}])
    ChunkAdapter().purge(Subject("chunk", s["parent"]))
    assert f"{OTHER}_0123456789abcdef" in s["col"].get(include=[])["ids"]


def test_an_id_with_no_artifact_purges_nothing(stores):
    assert ChunkAdapter().purge(Subject("chunk", "not-a-chunk-id")).removed == 0


def _entry(reg, cid: str, state: str) -> None:
    reg.append([Entry("fg_1", Subject("chunk", cid), state, "2026-10-09T10:00:00Z", "m1", "ui")])


def test_ingest_leaves_out_purged_passages_and_their_children(stores):
    from app.services.ingestion import _drop_purged_chunks

    s = stores
    _entry(s["reg"], s["parent"], "purged")
    records = [
        {"id": s["parent"], "parent_id": ""},
        {"id": s["child1"], "parent_id": s["parent"]},
        {"id": s["flat"], "parent_id": ""},
    ]
    kept, metas = _drop_purged_chunks(records, [{"i": 0}, {"i": 1}, {"i": 2}])
    assert [r["id"] for r in kept] == [s["flat"]]
    assert metas == [{"i": 2}]


def test_ingest_still_writes_a_trashed_passage(stores):
    from app.services.ingestion import _drop_purged_chunks

    s = stores
    _entry(s["reg"], s["flat"], "trashed")
    records = [{"id": s["flat"], "parent_id": ""}]
    assert _drop_purged_chunks(records, []) == (records, [])


def test_sync_import_skips_forgotten_passages(stores):
    from app.sync import import_

    s = stores
    _entry(s["reg"], s["parent"], "trashed")
    assert import_._is_forgotten_chunk(s["parent"], {})
    assert import_._is_forgotten_chunk(s["child1"], {"parent_chunk_id": s["parent"]})
    assert not import_._is_forgotten_chunk(s["flat"], {})
    assert import_._is_forgotten_lexical_row(s["parent"])
    assert not import_._is_forgotten_lexical_row(s["flat"])
    s["reg"].append([Entry("fg_2", Subject("artifact", AID), "trashed", "2026-10-09T10:00:00Z", "m1", "ui")])
    assert import_._is_forgotten_lexical_row(s["flat"])


# ---- ingest with purged passages, through ingest_content ----

def _ingest(content: str, prev: dict[str, Any] | None = None, collection: Any = None):
    from unittest.mock import MagicMock, patch

    from tests.test_ingest_atomicity import _make_collection_mock, _make_neo4j_mock

    collection = collection or _make_collection_mock()
    driver, _session = _make_neo4j_mock()
    with patch("app.services.ingestion.get_redis", return_value=MagicMock()), \
         patch("app.services.ingestion.get_neo4j", return_value=driver), \
         patch("app.services.ingestion.get_chroma") as chroma, \
         patch("app.services.ingestion.graph") as graph:
        chroma.return_value.get_or_create_collection.return_value = collection
        graph.find_artifact_by_filename.return_value = prev
        graph.find_artifact_by_external_id.return_value = None
        graph.create_artifact.return_value = None
        graph.discover_relationships.return_value = 0
        from app.services.ingestion import ingest_content
        result = ingest_content(content, domain="coding", metadata={"filename": "doc.md"}, skip_quality=True)
    return result, collection, graph


def _purge_all(reg, ids) -> None:
    reg.append([Entry("fg_9", Subject("chunk", c), "purged", "2026-10-09T10:00:00Z", "m1", "ui") for c in ids])


def test_a_document_whose_every_passage_was_purged_is_not_written(tmp_path, monkeypatch):
    reg = isolate_forget(monkeypatch, tmp_path)
    first, collection, _ = _ingest("hello world content")
    assert first["status"] == "success"
    _purge_all(reg, list(collection._stored))

    result, fresh, graph = _ingest("hello world content")
    assert result["status"] == "skipped" and result["reason"] == "forgotten"
    assert fresh._stored == {}
    fresh.upsert.assert_not_called()
    graph.create_artifact.assert_not_called()


def test_a_reingest_with_every_passage_purged_empties_the_node(tmp_path, monkeypatch):
    reg = isolate_forget(monkeypatch, tmp_path)
    content = "brand new re-ingested content that differs"
    _first, collection, _ = _ingest(content)
    _purge_all(reg, list(collection._stored))

    prev = {"id": "old-artifact-id", "content_hash": "stale-hash", "chunk_ids": "[]"}
    # The re-ingest keeps the old artifact id, so its ids differ; purge those too.
    import config
    from core.retrieval.chunk_ids import ChunkIdAssigner
    from utils.chunker import chunk_text, make_context_header
    header = make_context_header(filename="doc.md", domain="coding")
    texts = chunk_text(content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP, context_header=header)
    ids = ChunkIdAssigner("old-artifact-id")
    _purge_all(reg, [ids.assign("child", t) for t in texts])

    result, again, graph = _ingest(content, prev=prev)
    again.upsert.assert_not_called()
    assert graph.update_artifact.call_args.kwargs["chunk_count"] == 0
    assert graph.update_artifact.call_args.kwargs["chunk_ids_json"] == "[]"
    assert result["status"] != "error"


def test_bm25_import_of_a_new_domain_skips_forgotten_rows(stores, tmp_path, monkeypatch):
    from app.sync import import_

    s = stores
    _entry(s["reg"], s["flat"], "purged")
    src = tmp_path / "sync" / "bm25"
    src.mkdir(parents=True)
    rows = [{"id": s["flat"], "text": "gone"}, {"id": s["child1"], "text": "kept"}]
    (src / "general.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    dst = tmp_path / "local-bm25"
    monkeypatch.setattr("config.BM25_DATA_DIR", str(dst))
    import_.import_bm25(sync_dir=str(tmp_path / "sync"))
    kept = [json.loads(line)["id"] for line in (dst / "general.jsonl").read_text().splitlines()]
    assert kept == [s["child1"]]
