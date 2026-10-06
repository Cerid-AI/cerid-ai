# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A watched folder marked not searchable stays out of retrieval.

The stores are real where the behaviour lives in them: Chroma is an in-process
client, so the ``where`` clause is evaluated by Chroma and not by a double.
Redis and Neo4j are small fakes that hold what the code under test reads.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any
from unittest.mock import patch

import chromadb
import pytest

import config
from app.routers import watched_folders as wf
from app.services import folder_scanner
from core.agents import query_agent
from errors import RetrievalError
from utils import folder_privacy
from utils.folder_privacy import WATCHED_FOLDER_KEY, owning_folder_id

OFF = "folderoff0001"
SIBLING = "foldersib0002"


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.broken = False

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def incr(self, key: str) -> None:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)

    def smembers(self, key: str) -> set[str]:
        if self.broken:
            raise ConnectionError("redis is down")
        return set(self.sets.get(key, set()))

    def sadd(self, key: str, member: str) -> None:
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key: str, member: str) -> None:
        self.sets.get(key, set()).discard(member)


def _add_folder(redis: _FakeRedis, folder_id: str, path: str, **fields: Any) -> None:
    record = {
        "id": folder_id, "path": path, "label": path, "enabled": True,
        "exclude_patterns": [], "search_enabled": True, **fields,
    }
    wf._save_folder(redis, folder_id, record)
    wf._add_to_index(redis, folder_id)


@pytest.fixture
def redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(wf, "_get_redis", lambda: fake)
    monkeypatch.setattr(folder_scanner, "get_redis", lambda: fake)
    folder_privacy.invalidate_unsearchable_folders()
    query_agent.set_unsearchable_folder_provider(folder_privacy.unsearchable_folder_ids)
    yield fake
    query_agent.set_unsearchable_folder_provider(None)
    folder_privacy.invalidate_unsearchable_folders()


@pytest.fixture
def cache_flushes(monkeypatch):
    """Record query-cache flushes instead of reaching for a Redis server."""
    calls: list[str] = []
    monkeypatch.setattr(
        "utils.query_cache.invalidate_query_caches",
        lambda trigger, redis=None, domain=None: calls.append(trigger),
    )
    return calls


class _WordEmbedding(chromadb.EmbeddingFunction):
    """Deterministic bag-of-words vectors; no model download."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "folder_privacy_test_words"

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> _WordEmbedding:
        return _WordEmbedding()

    def __call__(self, input: Any) -> Any:  # noqa: A002 — Chroma's parameter name
        vectors = []
        for text in input:
            vec = [0.0] * 32
            for word in str(text).lower().split():
                vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 32] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class _Client:
    """The collections this test made, served with their embedding function."""

    def __init__(self) -> None:
        self._real = chromadb.EphemeralClient()
        self._collections: dict[str, Any] = {}

    def create(self, name: str) -> Any:
        col = self._real.get_or_create_collection(name, embedding_function=_WordEmbedding())
        self._collections[name] = col
        return col

    def get_collection(self, name: str) -> Any:
        return self._collections[name]

    def list_collections(self) -> list[Any]:
        return list(self._collections.values())

    def close(self) -> None:
        for name in self._collections:
            self._real.delete_collection(name)


def _chunk(artifact: str, folder: str | None, **extra: Any) -> dict[str, Any]:
    meta = {"artifact_id": artifact, "filename": f"{artifact}.md", "chunk_index": 0, **extra}
    if folder:
        meta[WATCHED_FOLDER_KEY] = folder
    return meta


@pytest.fixture
def kb():
    """One domain: a chunk from each of two folders, and one from neither."""
    client = _Client()
    domain = f"folderprivacy{uuid.uuid4().hex[:8]}"
    col = client.create(config.collection_name(domain))
    col.add(
        ids=["off_chunk_0", "sib_chunk_0", "plain_chunk_0"],
        documents=[
            "quarterly salary review notes",
            "quarterly salary survey for the industry",
            "quarterly planning notes",
        ],
        metadatas=[
            _chunk("off", OFF),
            _chunk("sib", SIBLING),
            _chunk("plain", None),
        ],
    )
    yield client, domain, col
    client.close()


async def _search(kb, *, top_k: int = 10) -> list[str]:
    client, domain, _ = kb
    with patch("core.retrieval.bm25.is_available", return_value=False):
        results = await query_agent.multi_domain_query(
            "quarterly salary review notes", domains=[domain], top_k=top_k,
            chroma_client=client,
        )
    return [r["chunk_id"] for r in results]


# ── the vector surface ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unsearchable_folder_is_left_out_and_its_sibling_is_not(redis, kb):
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)
    _add_folder(redis, SIBLING, "/archive/a/bc")

    ids = await _search(kb)

    assert "off_chunk_0" not in ids
    assert {"sib_chunk_0", "plain_chunk_0"} <= set(ids)


@pytest.mark.asyncio
async def test_every_folder_searchable_returns_everything(redis, kb):
    _add_folder(redis, OFF, "/archive/a/b")
    _add_folder(redis, SIBLING, "/archive/a/bc")

    assert set(await _search(kb)) == {"off_chunk_0", "sib_chunk_0", "plain_chunk_0"}


@pytest.mark.asyncio
async def test_excluded_chunk_does_not_use_up_a_result_slot(redis, kb):
    """The best match is the excluded one. top_k=1 must still return one."""
    _add_folder(redis, OFF, "/archive/a/b")
    assert await _search(kb, top_k=1) == ["off_chunk_0"]

    wf._save_folder(redis, OFF, {**wf._load_folder(redis, OFF), "search_enabled": False})
    folder_privacy.invalidate_unsearchable_folders()

    after = await _search(kb, top_k=1)
    assert len(after) == 1
    assert after != ["off_chunk_0"]


@pytest.mark.asyncio
async def test_toggle_through_the_api_takes_effect_at_once_and_is_reversible(
    redis, kb, cache_flushes, monkeypatch,
):
    async def no_earlier_content(path, *, exclude_patterns=None):
        return {"files": 0, "linked": 0, "unmatched": 0, "errored": 0}

    monkeypatch.setattr(folder_scanner, "link_folder_artifacts", no_earlier_content)
    _add_folder(redis, OFF, "/archive/a/b")
    assert "off_chunk_0" in await _search(kb)

    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(search_enabled=False))
    assert "off_chunk_0" not in await _search(kb)

    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(search_enabled=True))
    assert "off_chunk_0" in await _search(kb)

    # Answers cached under the previous set of folders are dropped each time.
    assert cache_flushes == ["watched_folders.search_enabled"] * 2


@pytest.mark.asyncio
async def test_change_that_leaves_the_toggle_alone_flushes_nothing(redis, cache_flushes):
    _add_folder(redis, OFF, "/archive/a/b")

    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(label="renamed"))
    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(search_enabled=True))

    assert cache_flushes == []


# ── the keyword arm ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_keyword_only_hit_from_unsearchable_folder_is_dropped(redis, kb):
    client, domain, col = kb
    col.add(
        ids=["off_kw_0", "sib_kw_0"],
        documents=["zebra", "zebra crossing"],
        metadatas=[_chunk("off_kw", OFF), _chunk("sib_kw", SIBLING)],
    )
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)
    _add_folder(redis, SIBLING, "/archive/a/bc")

    with patch("core.retrieval.bm25.is_available", return_value=True), \
         patch("core.retrieval.bm25.search_bm25",
               return_value=[("off_kw_0", 0.9), ("sib_kw_0", 0.8)]):
        results = await query_agent.multi_domain_query(
            "quarterly planning notes", domains=[domain], top_k=1, chroma_client=client,
        )

    ids = [r["chunk_id"] for r in results]
    assert "off_kw_0" not in ids
    assert "sib_kw_0" in ids


# ── graph expansion ──────────────────────────────────────────────────────────


class _GraphStore:
    def __init__(self, domain: str) -> None:
        self._domain = domain

    async def find_related_with_metadata(self, ids, depth, limit):
        return [
            {"id": aid, "domain": self._domain, "filename": f"{aid}.md",
             "chunk_ids": json.dumps([f"{aid}_chunk_0"])}
            for aid in ("off", "sib")
        ]


_SEED = [{"artifact_id": "plain", "chunk_id": "plain_chunk_0", "relevance": 0.5}]


@pytest.mark.asyncio
async def test_relationship_expansion_skips_unsearchable_folder(redis, kb):
    client, domain, _ = kb
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)

    results = await query_agent.graph_expand_results(
        list(_SEED), "quarterly salary", chroma_client=client,
        graph_store=_GraphStore(domain),
    )

    ids = {r["chunk_id"] for r in results}
    assert "sib_chunk_0" in ids
    assert "off_chunk_0" not in ids


@pytest.mark.asyncio
async def test_entity_expansion_skips_unsearchable_folder(redis, kb, monkeypatch):
    client, domain, _ = kb
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)
    monkeypatch.setattr(
        "core.retrieval.graphrag_retriever.entity_neighborhood_artifact_ids",
        lambda driver, seeds, top_k: [("off", 2), ("sib", 2)],
    )

    class _Driver:
        def execute_query(self, cypher, params):
            rows = [{"id": a, "domain": domain, "filename": f"{a}.md"} for a in params["ids"]]
            return rows, None, None

    results = await query_agent.graph_expand_results_via_entities(
        list(_SEED), "quarterly salary", chroma_client=client, neo4j_driver=_Driver(),
    )

    ids = {r["chunk_id"] for r in results}
    assert "sib_chunk_0" in ids
    assert "off_chunk_0" not in ids


@pytest.mark.asyncio
async def test_hype_hit_whose_parent_is_unsearchable_is_dropped(redis, kb):
    client, domain, _ = kb
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)

    hits = await query_agent._hydrate_hype_hits(
        client, config.collection_name(domain), domain,
        {"off_chunk_0": 0.9, "sib_chunk_0": 0.8},
    )

    assert [h["chunk_id"] for h in hits] == ["sib_chunk_0"]


# ── the entry every surface shares ───────────────────────────────────────────


async def _agent_query(kb) -> dict[str, Any]:
    client, domain, _ = kb
    no_verdict = {"contradiction": 0.0, "entailment": 0.0, "neutral": 1.0, "label": "neutral"}
    with patch("core.retrieval.bm25.is_available", return_value=False), \
         patch("core.utils.nli.batch_nli_score", side_effect=lambda pairs: [no_verdict] * len(pairs)), \
         patch.multiple(
             "config.features", ENABLE_SEMANTIC_CACHE=False,
             ENABLE_ADAPTIVE_RETRIEVAL=False, ENABLE_QUERY_DECOMPOSITION=False,
         ):
        return await query_agent.agent_query_full(
            "quarterly salary review notes", domains=[domain], strict_domains=True,
            chroma_client=client, use_reranking=False, external_augmentation=False,
        )


@pytest.mark.asyncio
async def test_agent_query_leaves_the_folder_out_of_results_sources_and_context(redis, kb):
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False,
                search_exclusion={"state": "complete"})
    _add_folder(redis, SIBLING, "/archive/a/bc")

    result = await _agent_query(kb)

    assert "off" not in {r["artifact_id"] for r in result["results"]}
    assert "off" not in {s["artifact_id"] for s in result["sources"]}
    assert "salary review" not in result["context"]
    assert "sib" in {r["artifact_id"] for r in result["results"]}
    assert not result.get("retrieval_degraded")


@pytest.mark.asyncio
async def test_folder_whose_earlier_content_is_not_linked_yet_is_reported(redis, kb):
    _add_folder(redis, OFF, "/archive/a/b", label="Payroll", search_enabled=False)

    result = await _agent_query(kb)

    assert result["retrieval_degraded"] is True
    assert "Payroll" in result["degraded_reason"]


# ── the list cannot be read ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unreadable_folder_list_fails_the_search(redis, kb):
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)
    redis.broken = True

    with pytest.raises(RetrievalError) as raised:
        await _search(kb)

    assert raised.value.error_code == "RETRIEVAL_FOLDER_EXCLUSIONS_UNREADABLE"


@pytest.mark.asyncio
async def test_unreadable_folder_list_fails_the_shared_entry(redis, kb):
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)
    redis.broken = True

    with pytest.raises(RetrievalError):
        await _agent_query(kb)


@pytest.mark.asyncio
async def test_list_is_read_once_for_many_queries(redis, kb):
    _add_folder(redis, OFF, "/archive/a/b", search_enabled=False)
    reads = 0
    real = redis.smembers

    def counting(key):
        nonlocal reads
        reads += 1
        return real(key)

    redis.smembers = counting
    for _ in range(3):
        await _search(kb)

    assert reads == 1


# ── which folder a file belongs to ───────────────────────────────────────────


_FOLDERS = [
    {"id": "b", "path": "/archive/a/b"},
    {"id": "bc", "path": "/archive/a/bc"},
    {"id": "spaced", "path": "/archive/My Files"},
    {"id": "nested", "path": "/archive/a/b/private"},
    {"id": "slash", "path": "/archive/trailing/"},
]


@pytest.mark.parametrize(("path", "owner"), [
    ("/archive/a/b/note.md", "b"),
    ("/archive/a/bc/note.md", "bc"),
    ("/archive/a/b/private/diary.md", "nested"),
    ("/archive/a/b/privateer/ship.md", "b"),
    ("/archive/My Files/tax return.pdf", "spaced"),
    ("/archive/My Files 2/tax return.pdf", None),
    ("/archive/trailing/x.md", "slash"),
    ("/archive/elsewhere/x.md", None),
])
def test_owning_folder_compares_whole_path_components(path, owner):
    assert owning_folder_id(path, _FOLDERS) == owner


# ── what the scanner writes ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scanner_stamps_each_file_with_its_own_folder(redis, tmp_path, monkeypatch):
    first = tmp_path / "My Files"
    second = tmp_path / "My Files 2"
    for folder in (first, second):
        folder.mkdir()
        (folder / "note.md").write_text(f"# Note\n\nKept in {folder.name}.\n")
    _add_folder(redis, OFF, str(first), search_enabled=False)
    _add_folder(redis, SIBLING, str(second))

    seen: dict[str, Any] = {}

    async def ingest_file(**kwargs):
        seen[kwargs["file_path"]] = kwargs.get("extra_metadata")
        return {"status": "success", "artifact_id": uuid.uuid4().hex, "quality_score": 0.9}

    monkeypatch.setattr(folder_scanner, "ingest_file", ingest_file)
    for folder in (first, second):
        async for _ in folder_scanner.scan_folder(str(folder), extensions={".md"}):
            pass

    assert seen[str(first / "note.md")] == {WATCHED_FOLDER_KEY: OFF}
    assert seen[str(second / "note.md")] == {WATCHED_FOLDER_KEY: SIBLING}


@pytest.mark.asyncio
async def test_scanner_outside_any_watched_folder_stamps_nothing(redis, tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("# Note\n\nNot in a watched folder.\n")
    seen: list[Any] = []

    async def ingest_file(**kwargs):
        seen.append(kwargs.get("extra_metadata"))
        return {"status": "success", "artifact_id": "a1", "quality_score": 0.9}

    monkeypatch.setattr(folder_scanner, "ingest_file", ingest_file)
    async for _ in folder_scanner.scan_folder(str(tmp_path), extensions={".md"}):
        pass

    assert seen == [None]


@pytest.mark.asyncio
async def test_scanner_links_content_that_was_already_in_the_kb(
    redis, kb, tmp_path, monkeypatch,
):
    client, domain, col = kb
    (tmp_path / "plain.md").write_text("quarterly planning notes")
    _add_folder(redis, OFF, str(tmp_path), search_enabled=False)
    monkeypatch.setattr(folder_scanner, "get_chroma", lambda: client)

    async def ingest_file(**kwargs):
        return {"status": "duplicate", "artifact_id": "plain", "domain": domain}

    monkeypatch.setattr(folder_scanner, "ingest_file", ingest_file)
    async for _ in folder_scanner.scan_folder(str(tmp_path), extensions={".md"}):
        pass

    assert col.get(ids=["plain_chunk_0"])["metadatas"][0][WATCHED_FOLDER_KEY] == OFF
    assert "plain_chunk_0" not in await _search(kb)


def test_stamping_covers_attachments_and_keeps_an_earlier_folder(kb, monkeypatch):
    client, domain, col = kb
    col.add(
        ids=["plain_attachment_0"],
        documents=["attached spreadsheet"],
        metadatas=[_chunk("attachment", None, parent_artifact_id="plain")],
    )
    monkeypatch.setattr(folder_scanner, "get_chroma", lambda: client)

    assert folder_scanner._stamp_artifact("plain", domain, OFF) == 2
    assert folder_scanner._stamp_artifact("sib", domain, OFF) == 0

    stamped = {
        cid: meta.get(WATCHED_FOLDER_KEY)
        for cid, meta in zip(*(col.get()[k] for k in ("ids", "metadatas")))
    }
    assert stamped == {
        "off_chunk_0": OFF, "sib_chunk_0": SIBLING,
        "plain_chunk_0": OFF, "plain_attachment_0": OFF,
    }
    # Stamping is a metadata update: the chunk keeps everything else.
    assert col.get(ids=["plain_chunk_0"])["metadatas"][0]["filename"] == "plain.md"


# ── content ingested before chunks named their folder ────────────────────────


class _Neo4j:
    """Artifacts by content hash and by filename, as the linking pass reads them."""

    def __init__(self, artifacts: list[dict[str, str]]) -> None:
        self.artifacts = artifacts

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher: str, **params: Any):
        if "content_hash" in cypher:
            rows = [a for a in self.artifacts if a["content_hash"] in params["hashes"]]
        else:
            rows = [a for a in self.artifacts if a["filename"] in params["names"]]
        return _Rows(rows)


class _Rows(list):
    def single(self, strict: bool = False):
        return self[0] if self else None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def earlier_content(redis, kb, tmp_path, monkeypatch):
    """Two folders with a colliding prefix, ingested before chunks were stamped."""
    client, domain, col = kb
    first = tmp_path / "b"
    second = tmp_path / "bc"
    first.mkdir()
    second.mkdir()
    (first / "pay.md").write_text("payroll figures")
    (first / "edited.md").write_text("this file changed after it was ingested")
    (first / "never.md").write_text("this file was never ingested")
    (second / "pay.md").write_text("pay scale survey")
    col.add(
        ids=["pay_b_0", "pay_bc_0", "edited_0"],
        documents=["payroll figures", "pay scale survey", "the text before the edit"],
        metadatas=[_chunk("pay_b", None), _chunk("pay_bc", None), _chunk("edited", None)],
    )
    artifacts = [
        {"id": "pay_b", "domain": domain, "filename": "pay.md", "content_hash": _sha("payroll figures")},
        {"id": "pay_bc", "domain": domain, "filename": "pay.md", "content_hash": _sha("pay scale survey")},
        {"id": "edited", "domain": domain, "filename": "edited.md", "content_hash": _sha("the text before the edit")},
    ]
    parsed: list[str] = []

    def parse(path: str) -> dict[str, Any]:
        parsed.append(path)
        with open(path) as handle:
            return {"text": handle.read()}

    monkeypatch.setattr(folder_scanner, "get_chroma", lambda: client)
    monkeypatch.setattr(folder_scanner, "get_neo4j", lambda: _Neo4j(artifacts))
    monkeypatch.setattr(folder_scanner, "_parse_file", parse)
    monkeypatch.setattr(config, "ENABLE_LAYOUT_AWARE_PARSING", False, raising=False)
    _add_folder(redis, OFF, str(first), search_enabled=False)
    _add_folder(redis, SIBLING, str(second))
    return first, second, col, parsed


@pytest.mark.asyncio
async def test_linking_matches_a_file_to_its_artifact_by_content(earlier_content):
    first, _, col, parsed = earlier_content

    counts = await folder_scanner.link_folder_artifacts(str(first))

    assert counts == {"files": 3, "linked": 1, "unmatched": 1, "errored": 0}
    metas = dict(zip(*(col.get()[k] for k in ("ids", "metadatas"))))
    assert metas["pay_b_0"][WATCHED_FOLDER_KEY] == OFF
    # Same file name in the sibling folder: a different artifact, untouched.
    assert WATCHED_FOLDER_KEY not in metas["pay_bc_0"]
    # Edited since it was ingested: its hash matches nothing, and it is counted.
    assert WATCHED_FOLDER_KEY not in metas["edited_0"]
    # A file no artifact is named after is not parsed at all.
    assert str(first / "never.md") not in parsed


@pytest.mark.asyncio
async def test_linked_content_leaves_retrieval_and_the_sibling_stays(earlier_content, kb):
    first, _, _, _ = earlier_content
    client, domain, _ = kb

    async def search() -> set[str]:
        with patch("core.retrieval.bm25.is_available", return_value=False):
            results = await query_agent.multi_domain_query(
                "payroll pay figures survey", domains=[domain], chroma_client=client,
            )
        return {r["chunk_id"] for r in results}

    assert {"pay_b_0", "pay_bc_0"} <= await search()

    await folder_scanner.link_folder_artifacts(str(first))

    after = await search()
    assert "pay_b_0" not in after
    assert "pay_bc_0" in after


@pytest.mark.asyncio
async def test_turning_the_toggle_off_links_and_records_the_outcome(
    earlier_content, redis, cache_flushes,
):
    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(search_enabled=True))
    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(search_enabled=False))
    await asyncio.gather(*wf._link_tasks)

    outcome = wf._load_folder(redis, OFF)["search_exclusion"]
    assert outcome["state"] == "complete"
    assert (outcome["linked"], outcome["unmatched"]) == (1, 1)
    assert wf._load_folder(redis, OFF)["search_enabled"] is False
    assert folder_privacy.exclusion_degraded_reason() == ""


@pytest.mark.asyncio
async def test_linking_that_fails_is_recorded_and_reported(
    earlier_content, redis, cache_flushes, monkeypatch,
):
    def unreachable():
        raise ConnectionError("neo4j is down")

    monkeypatch.setattr(folder_scanner, "get_neo4j", unreachable)
    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(search_enabled=True))
    await wf.update_watched_folder(OFF, wf.WatchedFolderUpdate(search_enabled=False))
    await asyncio.gather(*wf._link_tasks)

    assert wf._load_folder(redis, OFF)["search_exclusion"]["state"] == "failed"
    folder_privacy.unsearchable_folder_ids()
    assert str(earlier_content[0]) in folder_privacy.exclusion_degraded_reason()


# ── a re-ingest must not lose the folder ─────────────────────────────────────


def test_reingest_that_names_no_folder_keeps_the_one_on_the_old_chunks(kb, monkeypatch):
    import app.services.ingestion as ingestion

    client, domain, col = kb

    class _Chroma:
        def get_or_create_collection(self, name):
            return col

    monkeypatch.setattr(ingestion, "get_chroma", lambda: _Chroma())
    monkeypatch.setattr(ingestion, "get_neo4j", lambda: object())
    monkeypatch.setattr(ingestion, "get_redis", lambda: object())
    monkeypatch.setattr(
        "utils.query_cache.invalidate_query_caches_threaded", lambda *a, **k: None,
    )
    monkeypatch.setattr(config, "ENABLE_CONTEXTUAL_CHUNKS", False, raising=False)
    prev = {"id": "off", "content_hash": "old", "chunk_ids": json.dumps(["off_chunk_0"])}

    with patch("app.services.ingestion.graph"), \
         patch("app.services.ingestion.parent_child_enabled", return_value=False):
        ingestion._reingest_artifact(
            prev, "quarterly salary review notes, revised", domain,
            {"filename": "off.md"}, "new",
        )

    rewritten = col.get(where={"artifact_id": "off"})
    assert rewritten["ids"]
    assert all(m[WATCHED_FOLDER_KEY] == OFF for m in rewritten["metadatas"])
