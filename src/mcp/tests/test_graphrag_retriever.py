# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ChromaNeo4jRetriever (Phase 4a.5)."""
from __future__ import annotations

from unittest.mock import MagicMock

import neo4j
import pytest

from core.retrieval.graphrag_retriever import ChromaNeo4jRetriever


def _record(payload: dict) -> MagicMock:
    """Build a Pydantic-acceptable stand-in for ``neo4j.Record``.

    ``RawSearchResult`` validates that ``records`` are
    ``neo4j.Record`` instances; passing a real ``Record`` is awkward
    in unit tests, but ``MagicMock(spec=neo4j.Record)`` is treated as
    an instance by ``isinstance`` (and therefore by Pydantic).
    """
    rec = MagicMock(spec=neo4j.Record)
    rec.data.return_value = payload
    return rec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_chroma(ids: list[str], distances: list[float],
                 metadatas: list[dict] | None = None) -> MagicMock:
    """Return a duck-typed chromadb collection mock that yields canned results."""
    coll = MagicMock()
    coll.query.return_value = {
        "ids": [ids],
        "distances": [distances],
        "metadatas": [metadatas or [{} for _ in ids]],
    }
    return coll


def _fake_driver(records: list[dict] | None = None) -> MagicMock:
    """Return a duck-typed neo4j driver mock that returns canned records.

    `driver.execute_query` returns `(records, summary, keys)` per the
    neo4j Python-driver API.
    """
    driver = MagicMock()
    driver.execute_query.return_value = (
        [r if not isinstance(r, dict) else _record(r) for r in (records or [])],
        None, None,
    )
    return driver


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_defaults_for_artifact_level(self):
        r = ChromaNeo4jRetriever(
            driver=_fake_driver(),
            chroma_collection=_fake_chroma([], []),
            id_property_neo4j="id",
        )
        assert r.id_property_external == "artifact_id"
        assert r.id_property_neo4j == "id"
        assert r.node_label_neo4j == "Artifact"

    def test_chunk_level_mode(self):
        r = ChromaNeo4jRetriever(
            driver=_fake_driver(),
            chroma_collection=_fake_chroma([], []),
            id_property_neo4j="chunk_id",
            id_property_external="id",
            node_label_neo4j="Chunk",
        )
        assert r.id_property_external == "id"
        assert r.node_label_neo4j == "Chunk"


# ---------------------------------------------------------------------------
# get_search_results — happy paths
# ---------------------------------------------------------------------------

class TestSearch:
    def test_uses_metadata_artifact_id_for_match_params(self):
        coll = _fake_chroma(
            ids=["chunk-a", "chunk-b", "chunk-c"],
            distances=[0.1, 0.2, 0.3],
            metadatas=[
                {"artifact_id": "art-1"},
                {"artifact_id": "art-2"},
                {"artifact_id": "art-1"},  # same artifact as chunk-a
            ],
        )
        driver = _fake_driver(records=[{"node": "art-1"}, {"node": "art-2"}])
        r = ChromaNeo4jRetriever(
            driver=driver,
            chroma_collection=coll,
            id_property_neo4j="id",
            id_property_external="artifact_id",
        )
        result = r.get_search_results(query_vector=[0.1] * 768, top_k=3)

        # Cypher was invoked once
        driver.execute_query.assert_called_once()
        called_args = driver.execute_query.call_args
        cypher = called_args[0][0]
        params = called_args.kwargs["parameters_"]

        # Cypher tail follows the canonical UNWIND $match_params shape
        assert "UNWIND $match_params AS match_param" in cypher
        # Dedup happened: art-1 appears once, with the best (lowest-distance) score
        ids_in_params = [m[0] for m in params["match_params"]]
        assert ids_in_params.count("art-1") == 1
        assert ids_in_params.count("art-2") == 1
        # Best art-1 score is 1 - 0.1 = 0.9 (chunk-a beat chunk-c)
        scores = dict(params["match_params"])
        assert scores["art-1"] == pytest.approx(0.9)
        assert scores["art-2"] == pytest.approx(0.8)
        # RawSearchResult passes through neo4j records (count + types)
        assert len(result.records) == 2

    def test_chunk_level_uses_top_level_id_directly(self):
        coll = _fake_chroma(
            ids=["c1", "c2"],
            distances=[0.05, 0.5],
            metadatas=[{}, {}],
        )
        driver = _fake_driver(records=[{"chunk": "c1"}, {"chunk": "c2"}])
        r = ChromaNeo4jRetriever(
            driver=driver,
            chroma_collection=coll,
            id_property_neo4j="chunk_id",
            id_property_external="id",
            node_label_neo4j="Chunk",
        )
        r.get_search_results(query_vector=[0.0] * 768, top_k=2)

        params = driver.execute_query.call_args.kwargs["parameters_"]
        # Top-level chroma ids feed straight into match_params, no dedup needed
        match_ids = [m[0] for m in params["match_params"]]
        assert match_ids == ["c1", "c2"]
        assert params["id_property"] == "chunk_id"

    def test_skips_results_missing_metadata_key(self):
        coll = _fake_chroma(
            ids=["x", "y", "z"],
            distances=[0.1, 0.2, 0.3],
            metadatas=[
                {"artifact_id": "art-1"},
                {},  # missing — must be dropped
                {"artifact_id": "art-2"},
            ],
        )
        driver = _fake_driver()
        r = ChromaNeo4jRetriever(
            driver=driver, chroma_collection=coll,
            id_property_neo4j="id",
        )
        r.get_search_results(query_vector=[0.0] * 4, top_k=3)
        params = driver.execute_query.call_args.kwargs["parameters_"]
        ids_in_params = [m[0] for m in params["match_params"]]
        assert ids_in_params == ["art-1", "art-2"]

    def test_where_filter_forwarded_to_chroma(self):
        coll = _fake_chroma(["a"], [0.0], [{"artifact_id": "art-x"}])
        r = ChromaNeo4jRetriever(
            driver=_fake_driver(), chroma_collection=coll,
            id_property_neo4j="id",
        )
        r.get_search_results(
            query_vector=[0.0], top_k=1,
            where={"domain": {"$eq": "trading"}},
        )
        # Chroma got the where filter
        call_kwargs = coll.query.call_args.kwargs
        assert call_kwargs["where"] == {"domain": {"$eq": "trading"}}

    def test_query_text_requires_embedder(self):
        from neo4j_graphrag.exceptions import EmbeddingRequiredError

        r = ChromaNeo4jRetriever(
            driver=_fake_driver(), chroma_collection=_fake_chroma([], []),
            id_property_neo4j="id",
        )
        with pytest.raises(EmbeddingRequiredError):
            r.get_search_results(query_text="hello")

    def test_query_text_uses_embedder(self):
        embedder = MagicMock()
        embedder.embed_query.return_value = [0.1, 0.2, 0.3]
        coll = _fake_chroma(["a"], [0.05], [{"artifact_id": "art-1"}])
        r = ChromaNeo4jRetriever(
            driver=_fake_driver(),
            chroma_collection=coll,
            id_property_neo4j="id",
            embedder=embedder,
        )
        r.get_search_results(query_text="find me things", top_k=1)
        embedder.embed_query.assert_called_once_with("find me things")
        # The embedded vector was passed to chroma
        assert coll.query.call_args.kwargs["query_embeddings"] == [[0.1, 0.2, 0.3]]

    def test_empty_chroma_result_yields_no_match_params(self):
        coll = _fake_chroma([], [], [])
        driver = _fake_driver()
        r = ChromaNeo4jRetriever(
            driver=driver, chroma_collection=coll, id_property_neo4j="id",
        )
        r.get_search_results(query_vector=[0.0] * 4, top_k=5)
        params = driver.execute_query.call_args.kwargs["parameters_"]
        assert params["match_params"] == []
