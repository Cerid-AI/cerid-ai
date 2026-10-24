# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F146 — the HyPE retrieval leg must not be a side door into ChromaDB.

``_augment_with_hype`` queried the HyPE question collections with no ``where``
clause at all, while the documented chokepoint for every other Chroma query in
the retrieval path is ``_exclude_pending(with_tenant_scope(...))``. The HyPE
writer stamps neither ``tenant_id`` nor ``cerid_state``, so the only way to
apply the request's scope to a HyPE hit is to resolve its parent chunk through
the scoped path — which also replaces the LLM-generated hypothetical question
with the parent's real text before it reaches the context assembler.
"""
from __future__ import annotations

from typing import Any

import pytest


class _FakeHypeCollection:
    def __init__(self, hits: list[tuple[str, str, dict[str, Any]]]) -> None:
        self._hits = hits

    def query(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "ids": [[h[0] for h in self._hits]],
            "documents": [[h[1] for h in self._hits]],
            "metadatas": [[h[2] for h in self._hits]],
            "distances": [[0.1 for _ in self._hits]],
        }


class _FakeBaseCollection:
    """Parent-chunk store. Records the ``where`` used for hydration."""

    def __init__(self, rows: dict[str, tuple[str, dict[str, Any]]]) -> None:
        self._rows = rows
        self.get_calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        ids = [i for i in kwargs.get("ids", []) if i in self._rows]
        return {
            "ids": ids,
            "documents": [self._rows[i][0] for i in ids],
            "metadatas": [self._rows[i][1] for i in ids],
        }


class _FakeChroma:
    def __init__(self, collections: dict[str, Any]) -> None:
        self._collections = collections

    def get_collection(self, name: str) -> Any:
        if name not in self._collections:
            raise ValueError(f"no such collection: {name}")
        return self._collections[name]


@pytest.fixture
def _hype_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    from core.utils import embeddings as emb

    monkeypatch.setattr(emb, "get_embedding_function", lambda: (lambda texts: [[0.1, 0.2]]))


@pytest.mark.asyncio
@pytest.mark.usefixtures("_hype_on")
async def test_hype_only_hit_is_hydrated_from_its_parent_chunk() -> None:
    """The generated question must never become the retrieved content."""
    import config
    from core.agents import query_agent
    from core.retrieval.hype_index import hype_collection_name

    base_name = config.collection_name("general")
    base = _FakeBaseCollection({
        "chunk-1": ("The real body text of the source document.",
                    {"filename": "notes.md", "artifact_id": "art-1", "chunk_index": 3}),
    })
    hype = _FakeHypeCollection([
        ("hype-1", "What did the founder decide about pricing?",
         {"source_chunk_id": "chunk-1", "source_artifact_id": "art-1"}),
    ])
    client = _FakeChroma({base_name: base, hype_collection_name(base_name): hype})

    merged = await query_agent._augment_with_hype(
        query="pricing decision", results=[], chroma_client=client, domains=["general"],
    )

    assert len(merged) == 1, merged
    hit = merged[0]
    assert hit["content"] == "The real body text of the source document."
    assert hit["filename"] == "notes.md"
    assert hit["domain"] == "general"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_hype_on")
async def test_hype_hydration_applies_tenant_and_pending_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent lookup must carry the tenant + pending-exclude clause."""
    import config
    from core.agents import query_agent
    from core.context.identity import tenant_id_var
    from core.retrieval.hype_index import hype_collection_name

    monkeypatch.setenv("CERID_MULTI_USER", "true")
    base_name = config.collection_name("general")
    base = _FakeBaseCollection({
        "chunk-1": ("body", {"filename": "notes.md"}),
    })
    hype = _FakeHypeCollection([
        ("hype-1", "a question", {"source_chunk_id": "chunk-1"}),
    ])
    client = _FakeChroma({base_name: base, hype_collection_name(base_name): hype})

    token = tenant_id_var.set("alice")
    try:
        await query_agent._augment_with_hype(
            query="q", results=[], chroma_client=client, domains=["general"],
        )
    finally:
        tenant_id_var.reset(token)

    assert base.get_calls, "parent chunks were never fetched — HyPE bypassed the scope"
    where = base.get_calls[0].get("where")
    assert where == {
        "$and": [{"tenant_id": "alice"}, {"cerid_state": {"$ne": "pending"}}]
    }, f"HyPE parent lookup does not enforce tenant + pending scope: {where!r}"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_hype_on")
async def test_hype_hydration_carries_the_consumer_record_type_scope_and_the_caller_filter() -> None:
    """A consumer allowed one record_type in a domain (cerid-finance reads only
    mail_financial_card rows of inbox) must not receive other rows of that
    domain through a HyPE hit; the caller's own filter rides along too."""
    import json

    import config
    from core.agents import query_agent
    from core.retrieval.hype_index import hype_collection_name

    base_name = config.collection_name("inbox")
    base = _FakeBaseCollection({
        "chunk-1": ("a mail body", {"filename": "mail.eml", "record_type": "mail_body"}),
    })
    hype = _FakeHypeCollection([
        ("hype-1", "a question", {"source_chunk_id": "chunk-1"}),
    ])
    client = _FakeChroma({base_name: base, hype_collection_name(base_name): hype})

    await query_agent._augment_with_hype(
        query="q", results=[], chroma_client=client, domains=["inbox"],
        metadata_filter={"source": "statement"},
        domain_record_types={"inbox": ["mail_financial_card"]},
    )

    assert base.get_calls, "parent chunks were never fetched"
    where = json.dumps(base.get_calls[0].get("where"), sort_keys=True)
    assert '"record_type": "mail_financial_card"' in where, where
    assert '"source": "statement"' in where, where
    assert '"cerid_state": {"$ne": "pending"}' in where, where


@pytest.mark.asyncio
@pytest.mark.usefixtures("_hype_on")
async def test_unhydratable_hype_hit_is_dropped() -> None:
    """A hit whose parent is out of scope must not reach the caller."""
    import config
    from core.agents import query_agent
    from core.retrieval.hype_index import hype_collection_name

    base_name = config.collection_name("general")
    base = _FakeBaseCollection({})  # parent invisible to this request
    hype = _FakeHypeCollection([
        ("hype-1", "leaked question text", {"source_chunk_id": "chunk-1"}),
    ])
    client = _FakeChroma({base_name: base, hype_collection_name(base_name): hype})

    merged = await query_agent._augment_with_hype(
        query="q", results=[], chroma_client=client, domains=["general"],
    )

    assert merged == [], f"out-of-scope HyPE hit surfaced: {merged!r}"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_hype_on")
async def test_hype_boost_of_existing_content_hit_still_works() -> None:
    """Hydration must not break the boost path for chunks already retrieved."""
    import config
    from core.agents import query_agent
    from core.retrieval.hype_index import hype_collection_name

    base_name = config.collection_name("general")
    base = _FakeBaseCollection({"chunk-1": ("body", {"filename": "notes.md"})})
    hype = _FakeHypeCollection([
        ("hype-1", "a question", {"source_chunk_id": "chunk-1"}),
    ])
    client = _FakeChroma({base_name: base, hype_collection_name(base_name): hype})

    existing = {"chunk_id": "chunk-1", "content": "body", "relevance": 0.1,
                "domain": "general", "filename": "notes.md"}
    merged = await query_agent._augment_with_hype(
        query="q", results=[existing], chroma_client=client, domains=["general"],
    )

    assert len(merged) == 1
    assert merged[0]["relevance"] > 0.1
    assert merged[0]["content"] == "body"


# F131 — a HyPE-only hit carries the metadata a content hit for the same chunk
# carries, the consumer's custom ingest metadata included.
_CUSTOM = {"repo_sig": "abc123", "importance": "high"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_hype_on")
async def test_hype_only_hit_keeps_the_chunks_custom_metadata() -> None:
    import config
    from core.agents import query_agent
    from core.retrieval.hype_index import hype_collection_name

    stored = {"filename": "notes.md", "artifact_id": "art-1", "chunk_index": 3, **_CUSTOM}
    base_name = config.collection_name("general")
    base = _FakeBaseCollection({"chunk-1": ("body", stored)})
    hype = _FakeHypeCollection([
        ("hype-1", "a question", {"source_chunk_id": "chunk-1", "source_artifact_id": "art-1"}),
    ])
    client = _FakeChroma({base_name: base, hype_collection_name(base_name): hype})

    merged = await query_agent._augment_with_hype(
        query="q", results=[], chroma_client=client, domains=["general"],
    )

    as_content_hit = query_agent._format_chroma_result(
        content="body", relevance=0.5, chunk_id="chunk-1", domain="general", metadata=stored,
    )
    assert merged[0].get("metadata") == as_content_hit["metadata"] == _CUSTOM
    assert merged[0]["artifact_id"] == "art-1"


class _FakeDomainCollection(_FakeBaseCollection):
    """A domain collection whose content search returns one chunk."""

    def __init__(self, rows: dict[str, tuple[str, dict[str, Any]]], content_hit: str) -> None:
        super().__init__(rows)
        self._content_hit = content_hit

    def count(self) -> int:
        return len(self._rows)

    def query(self, **kwargs: Any) -> dict[str, Any]:
        doc, meta = self._rows[self._content_hit]
        return {"ids": [[self._content_hit]], "documents": [[doc]],
                "metadatas": [[meta]], "distances": [[0.3]]}

    def get(self, **kwargs: Any) -> dict[str, Any]:
        if "ids" not in kwargs:
            self.get_calls.append(kwargs)
            return {"ids": [], "documents": [], "metadatas": []}
        return super().get(**kwargs)


class _ListingChroma(_FakeChroma):
    def list_collections(self) -> list[Any]:
        from types import SimpleNamespace

        return [SimpleNamespace(name=n) for n in self._collections]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_hype_on")
async def test_agent_query_returns_custom_metadata_on_content_and_hype_hits() -> None:
    """Through the real agent_query: one chunk found by its content, another
    only by its hypothetical question. Both answer with their own metadata."""
    from unittest.mock import patch

    import config
    from core.agents.query_agent import agent_query
    from core.retrieval.hype_index import hype_collection_name
    from tests.test_rag_pipeline_integration import _FEATURE_OVERRIDES, _mock_neo4j

    def _meta(name: str, custom: dict[str, Any]) -> dict[str, Any]:
        return {"domain": "coding", "filename": name, "artifact_id": name, "chunk_index": 0,
                "ingested_at": "", "sub_category": "", "tags_json": "[]", "keywords": "[]",
                **custom}

    by_content = {"repo_sig": "content-sig"}
    base_name = config.collection_name("coding")
    base = _FakeDomainCollection({
        "c-content": ("Spark caches a DataFrame with cache().", _meta("spark.md", by_content)),
        "c-hype": ("Persist with StorageLevel.MEMORY_AND_DISK.", _meta("persist.md", _CUSTOM)),
    }, content_hit="c-content")
    hype = _FakeHypeCollection([
        ("c-hype_hype_0", "how do I keep a DataFrame in memory?",
         {"source_chunk_id": "c-hype", "source_artifact_id": "persist.md"}),
    ])
    client = _ListingChroma({base_name: base, hype_collection_name(base_name): hype})
    features: dict[str, Any] = {k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}
    neutral = {"contradiction": 0.0, "entailment": 0.0, "neutral": 1.0, "label": "neutral"}

    with (
        patch.multiple("config.features", **features),
        patch("core.retrieval.bm25.is_available", return_value=False),
        patch("core.utils.nli.batch_nli_score", side_effect=lambda pairs: [neutral] * len(pairs)),
    ):
        result = await agent_query(
            query="how do I cache a DataFrame", domains=["coding"], strict_domains=True,
            chroma_client=client, neo4j_driver=_mock_neo4j(), use_reranking=False,
            skip_cache=True,
        )

    by_file = {r["filename"]: r for r in result["results"]}
    assert by_file["persist.md"].get("hype_source") is True, by_file
    assert by_file["spark.md"]["metadata"] == by_content
    assert by_file["persist.md"].get("metadata") == _CUSTOM
