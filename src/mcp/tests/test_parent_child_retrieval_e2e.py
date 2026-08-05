# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""End-to-end tests for parent-child retrieval (RAG C2.6).

Covers the two wire-in sites that bridge the existing
``chunk_with_parents`` helper into the live ingest + query paths:

* Ingest side — ``app.services.ingestion.ingest_content`` writes BOTH
  parent and child rows to Chroma with ``chunk_level`` +
  ``parent_chunk_id`` metadata when the feature flag is on, and uniform
  ``chunk_level="child"`` / empty ``parent_chunk_id`` when it's off.
* Query side — ``core.agents.query_agent.multi_domain_query`` filters
  to children at retrieval time and substitutes parent text into each
  result's ``content`` before returning.

The tests stub Chroma + Neo4j so they run as fast unit-level e2e
without requiring the docker sandbox. A live-stack ingest/query test
against ``127.0.0.1:8898`` would belong in
``tests/integration/`` — out of scope for this checkpoint.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Fake ChromaDB collection ──────────────────────────────────────────────


class _FakeCollection:
    """Tiny in-memory Chroma stand-in for the multi-domain query path.

    Supports the operations the parent-child query path exercises:
    ``add`` (used by ingest), ``query`` (vector-search returning the top
    N child rows), ``get`` (used to fetch parent texts by ID), and
    ``update`` / ``delete`` for the pending-state machinery.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    # ── Chroma surface used by ingest ───────────────────────────────────

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for cid, doc, meta in zip(ids, documents, metadatas):
            self.rows[cid] = {"document": doc, "metadata": dict(meta)}

    # ingest_content upserts (content-addressed ids); storage already overwrites
    # by id, so upsert == add here.
    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.add(ids, documents, metadatas)

    def update(
        self,
        ids: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for cid, patch_md in zip(ids, metadatas):
            if cid in self.rows:
                self.rows[cid]["metadata"].update(patch_md)

    def delete(self, ids: list[str]) -> None:
        for cid in ids:
            self.rows.pop(cid, None)

    # ── Chroma surface used by query ────────────────────────────────────

    def _matches_where(self, meta: dict[str, Any], where: dict[str, Any] | None) -> bool:
        if not where:
            return True
        if "$and" in where:
            return all(self._matches_where(meta, clause) for clause in where["$and"])
        for key, expected in where.items():
            if key == "$and":
                continue
            if isinstance(expected, dict) and "$ne" in expected:
                if meta.get(key) == expected["$ne"]:
                    return False
            else:
                if meta.get(key) != expected:
                    return False
        return True

    def query(
        self,
        query_texts: list[str],
        n_results: int,
        include: list[str],
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Return child rows in deterministic insertion order, filtered by
        # where, capped to n_results.  Distance is monotonically increasing
        # so the test can sort by relevance and assert ordering.
        matches: list[tuple[str, dict[str, Any]]] = [
            (cid, row)
            for cid, row in self.rows.items()
            if self._matches_where(row["metadata"], where)
        ]
        matches = matches[:n_results]
        ids = [cid for cid, _ in matches]
        docs = [row["document"] for _, row in matches]
        metas = [row["metadata"] for _, row in matches]
        distances = [0.1 + i * 0.05 for i in range(len(matches))]
        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [metas],
            "distances": [distances],
        }

    def get(
        self,
        ids: list[str],
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        out_ids: list[str] = []
        out_docs: list[str] = []
        out_metas: list[dict[str, Any]] = []
        for cid in ids:
            row = self.rows.get(cid)
            if row is None:
                continue
            out_ids.append(cid)
            out_docs.append(row["document"])
            out_metas.append(row["metadata"])
        return {"ids": out_ids, "documents": out_docs, "metadatas": out_metas}


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_collection() -> _FakeCollection:
    return _FakeCollection()


@pytest.fixture
def fake_chroma_client(fake_collection: _FakeCollection):
    client = MagicMock()
    client.get_or_create_collection.return_value = fake_collection
    client.get_collection.return_value = fake_collection
    coll_metadata = MagicMock()
    coll_metadata.name = "domain_general"
    client.list_collections.return_value = [coll_metadata]
    return client


@pytest.fixture
def fake_neo4j_driver() -> MagicMock:
    driver = MagicMock()
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.single.return_value = None
    result_mock.data.return_value = []
    session.run.return_value = result_mock
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = session
    return driver


@pytest.fixture
def long_text() -> str:
    """Generate ~1500 words of distinguishable text."""
    paragraphs: list[str] = []
    for i in range(30):
        para = (
            f"Section {i} discusses observability budgets, "
            "context-window discipline, parent-child chunking, "
            "and the dialog between retrieval precision and "
            "generation richness. " * 2
        )
        paragraphs.append(para)
    return "\n\n".join(paragraphs)


def _enable_parent_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_PARENT_CHILD_RETRIEVAL", "true")
    import utils.chunker as chunker_mod
    monkeypatch.setattr(chunker_mod, "PARENT_CHILD_ENABLED", True)


def _disable_parent_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_PARENT_CHILD_RETRIEVAL", "false")
    import utils.chunker as chunker_mod
    monkeypatch.setattr(chunker_mod, "PARENT_CHILD_ENABLED", False)


# ── Phase A — ingest writes both classes of chunks ───────────────────────


class TestIngestWritesParentAndChild:
    def test_flag_on_writes_parent_and_child_rows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_chroma_client,
        fake_collection: _FakeCollection,
        fake_neo4j_driver,
        long_text: str,
    ) -> None:
        _enable_parent_child(monkeypatch)

        with patch("app.services.ingestion.get_chroma", return_value=fake_chroma_client), \
             patch("app.services.ingestion.get_neo4j", return_value=fake_neo4j_driver), \
             patch("app.services.ingestion.get_redis", return_value=MagicMock()), \
             patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0
            mock_graph.resolve_pending_artifacts.return_value = None

            from app.services.ingestion import ingest_content

            result = ingest_content(content=long_text, domain="general")

        assert result["status"] == "success"

        levels = {row["metadata"].get("chunk_level") for row in fake_collection.rows.values()}
        assert "parent" in levels, "expected at least one parent row in Chroma"
        assert "child" in levels, "expected at least one child row in Chroma"

        # Every child row must carry a parent_chunk_id pointing at a
        # parent row that was written.
        parent_ids = {
            cid for cid, row in fake_collection.rows.items()
            if row["metadata"].get("chunk_level") == "parent"
        }
        for cid, row in fake_collection.rows.items():
            if row["metadata"].get("chunk_level") == "child":
                pid = row["metadata"].get("parent_chunk_id")
                assert pid, f"child {cid} missing parent_chunk_id"
                assert pid in parent_ids, (
                    f"child {cid} references unknown parent {pid}"
                )

    def test_flag_off_uniform_child_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_chroma_client,
        fake_collection: _FakeCollection,
        fake_neo4j_driver,
        long_text: str,
    ) -> None:
        _disable_parent_child(monkeypatch)

        with patch("app.services.ingestion.get_chroma", return_value=fake_chroma_client), \
             patch("app.services.ingestion.get_neo4j", return_value=fake_neo4j_driver), \
             patch("app.services.ingestion.get_redis", return_value=MagicMock()), \
             patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0
            mock_graph.resolve_pending_artifacts.return_value = None

            from app.services.ingestion import ingest_content

            result = ingest_content(content=long_text, domain="general")

        assert result["status"] == "success"

        # Uniform child metadata even when flag is off — design lock so
        # the query-side filter doesn't need a runtime branch on flag state.
        levels = {row["metadata"].get("chunk_level") for row in fake_collection.rows.values()}
        assert levels == {"child"}, f"expected uniform child level, got {levels}"
        for row in fake_collection.rows.values():
            assert row["metadata"].get("parent_chunk_id", "") == "", (
                "flag-off rows must carry empty parent_chunk_id"
            )


# ── Phase B — query substitutes parent text ──────────────────────────────


class TestQuerySubstitutesParent:
    def test_query_returns_parent_content_with_child_relevance(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_chroma_client,
        fake_collection: _FakeCollection,
    ) -> None:
        _enable_parent_child(monkeypatch)

        # Seed the collection by hand with one parent + two children.
        fake_collection.rows["art1_parent_0"] = {
            "document": "PARENT_TEXT: full paragraph about budgets and retrieval.",
            "metadata": {
                "artifact_id": "art1",
                "filename": "doc.md",
                "domain": "general",
                "chunk_index": 0,
                "chunk_level": "parent",
                "parent_chunk_id": "",
                "tenant_id": "default",
            },
        }
        fake_collection.rows["art1_child_0_0"] = {
            "document": "child snippet about budgets",
            "metadata": {
                "artifact_id": "art1",
                "filename": "doc.md",
                "domain": "general",
                "chunk_index": 1,
                "chunk_level": "child",
                "parent_chunk_id": "art1_parent_0",
                "tenant_id": "default",
            },
        }
        fake_collection.rows["art1_child_0_1"] = {
            "document": "child snippet about retrieval",
            "metadata": {
                "artifact_id": "art1",
                "filename": "doc.md",
                "domain": "general",
                "chunk_index": 2,
                "chunk_level": "child",
                "parent_chunk_id": "art1_parent_0",
                "tenant_id": "default",
            },
        }

        from core.agents.query_agent import multi_domain_query

        with patch("core.retrieval.bm25.is_available", return_value=False):
            results = asyncio.run(
                multi_domain_query(
                    "budgets",
                    domains=["general"],
                    top_k=5,
                    chroma_client=fake_chroma_client,
                )
            )

        assert results, "expected at least one ranked result"
        # The where-clause must have filtered out the parent row — only
        # children get ranked.
        for r in results:
            assert r["chunk_level"] == "child", (
                f"ranked non-child row {r['chunk_id']} — child filter not applied"
            )
            # Parent text was substituted into content.
            assert r["content"].startswith("PARENT_TEXT:"), (
                f"child {r['chunk_id']} did not receive parent substitution; "
                f"got content={r['content']!r}"
            )
            assert r.get("parent_substituted") is True
            # Score was preserved (non-zero, from the vector match against
            # the child row).
            assert r["relevance"] > 0


class TestQueryMixedCorpus:
    def test_child_without_parent_falls_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_chroma_client,
        fake_collection: _FakeCollection,
    ) -> None:
        """Children with empty parent_chunk_id keep their own content.

        Models a mixed corpus where some artifacts were ingested with the
        flag off (uniform child rows, no parents) and others with the flag
        on (paired parent/child rows). Query path runs with flag on.
        """
        _enable_parent_child(monkeypatch)

        # Flag-off-style row: child with empty parent_chunk_id.
        fake_collection.rows["legacy_chunk_0"] = {
            "document": "LEGACY_CONTENT: no parent linkage.",
            "metadata": {
                "artifact_id": "legacy",
                "filename": "old.md",
                "domain": "general",
                "chunk_index": 0,
                "chunk_level": "child",
                "parent_chunk_id": "",
                "tenant_id": "default",
            },
        }
        # Flag-on-style: parent + child.
        fake_collection.rows["art2_parent_0"] = {
            "document": "PARENT_TEXT: art2 paragraph.",
            "metadata": {
                "artifact_id": "art2",
                "filename": "new.md",
                "domain": "general",
                "chunk_index": 0,
                "chunk_level": "parent",
                "parent_chunk_id": "",
                "tenant_id": "default",
            },
        }
        fake_collection.rows["art2_child_0_0"] = {
            "document": "child snippet of art2",
            "metadata": {
                "artifact_id": "art2",
                "filename": "new.md",
                "domain": "general",
                "chunk_index": 1,
                "chunk_level": "child",
                "parent_chunk_id": "art2_parent_0",
                "tenant_id": "default",
            },
        }

        from core.agents.query_agent import multi_domain_query

        with patch("core.retrieval.bm25.is_available", return_value=False):
            results = asyncio.run(
                multi_domain_query(
                    "anything",
                    domains=["general"],
                    top_k=10,
                    chroma_client=fake_chroma_client,
                )
            )

        by_id = {r["chunk_id"]: r for r in results}
        assert "legacy_chunk_0" in by_id, (
            "child rows without parents must still be returned"
        )
        assert by_id["legacy_chunk_0"]["content"] == "LEGACY_CONTENT: no parent linkage."
        assert by_id["legacy_chunk_0"].get("parent_substituted") is not True
        assert "art2_child_0_0" in by_id
        assert by_id["art2_child_0_0"]["content"].startswith("PARENT_TEXT:")
        assert by_id["art2_child_0_0"].get("parent_substituted") is True


# ── Phase B — non-regression for flag-off path ───────────────────────────


class TestQueryNonRegressionFlagOff:
    def test_flag_off_returns_chunks_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_chroma_client,
        fake_collection: _FakeCollection,
    ) -> None:
        """With the flag off, every chunk is a "child" with empty parent
        link; the query path must not invoke the substitution helper and
        the content must be the original chunk text.
        """
        _disable_parent_child(monkeypatch)

        fake_collection.rows["legacy_chunk_0"] = {
            "document": "ORIGINAL CONTENT",
            "metadata": {
                "artifact_id": "art1",
                "filename": "doc.md",
                "domain": "general",
                "chunk_index": 0,
                "chunk_level": "child",
                "parent_chunk_id": "",
                "tenant_id": "default",
            },
        }

        from core.agents.query_agent import multi_domain_query

        with patch("core.retrieval.bm25.is_available", return_value=False):
            results = asyncio.run(
                multi_domain_query(
                    "anything",
                    domains=["general"],
                    top_k=5,
                    chroma_client=fake_chroma_client,
                )
            )

        assert len(results) == 1
        assert results[0]["content"] == "ORIGINAL CONTENT"
        assert results[0].get("parent_substituted") is not True
