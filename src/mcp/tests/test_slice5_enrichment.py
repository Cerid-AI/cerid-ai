# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Slice 5.1 — the ingestion enrichment seam (RAG Quality Program 2026-06-12).

Pins the contract:

- ``enrich=True`` (default): when the caller supplied neither tags nor
  sub_category, ``ingest_content`` classifies them via ``ai_categorize`` so
  the memory / connector / digest / text_input paths get the same
  wiki-granularity + tag-sorting metadata as file uploads.
- ``enrich=False``: skips the re-classify (triage already classified).
- Enrichment NEVER changes ``domain`` — including the conversations carve-out,
  which is satisfied for free (domain is never touched).
- Caller-supplied tags suppress enrichment of tags.
- Classifier failure → ingest proceeds untagged (never blocks).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_stores(mock_chroma, mock_neo4j, mock_redis):
    collection = MagicMock()
    collection.count.return_value = 0
    mock_chroma.return_value.get_or_create_collection.return_value = collection

    driver = MagicMock()
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.single.return_value = None
    result_mock.data.return_value = []
    session.run.return_value = result_mock
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = session
    mock_neo4j.return_value = driver
    mock_redis.return_value = MagicMock()


def _prep_graph(mock_graph):
    """Make the patched graph module return ints where ingest_content does
    arithmetic/comparisons downstream of create_artifact."""
    mock_graph.discover_relationships.return_value = 0


def _create_artifact_kwargs(mock_graph):
    """Return the kwargs create_artifact was called with, or None."""
    if not mock_graph.create_artifact.called:
        return None
    return mock_graph.create_artifact.call_args.kwargs


@patch("app.services.ingestion.graph")
@patch("app.services.ingestion.get_redis")
@patch("app.services.ingestion.get_neo4j")
@patch("app.services.ingestion.get_chroma")
@patch("app.services.ingestion.extract_metadata", new_callable=AsyncMock)
@patch("app.services.ingestion.ai_categorize", new_callable=AsyncMock)
class TestEnrichmentSeam:
    def test_enrich_default_classifies_when_no_tags_or_subcat(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        mock_meta.return_value = {"summary": "s"}
        mock_cat.return_value = {
            "suggested_domain": "finance", "sub_category": "trading",
            "tags": ["Earnings", "q3-report"], "keywords": [], "summary": "s",
        }
        _mock_stores(mock_chroma, mock_neo4j, mock_redis)
        _prep_graph(mock_graph)

        from app.services.ingestion import ingest_content

        ingest_content("a quarterly earnings note", domain="general")

        mock_cat.assert_called_once()
        kw = _create_artifact_kwargs(mock_graph)
        assert kw is not None, "create_artifact was not reached"
        assert kw["sub_category"] == "trading"
        assert sorted(json.loads(kw["tags_json"])) == ["earnings", "q3-report"]
        # domain unchanged — enrichment must never move the collection.
        assert kw["domain"] == "general"

    def test_enrich_false_skips_classifier(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        mock_meta.return_value = {"summary": "s"}
        mock_cat.return_value = {"sub_category": "trading", "tags": ["x"]}
        _mock_stores(mock_chroma, mock_neo4j, mock_redis)
        _prep_graph(mock_graph)

        from app.services.ingestion import ingest_content

        ingest_content("triage already classified me", domain="general", enrich=False)

        mock_cat.assert_not_called()

    def test_conversations_domain_keeps_domain_gets_subcat(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        """HARD CONSTRAINT — conversations is never re-domained. Enrichment
        only adds sub_category + tags; domain stays 'conversations'."""
        mock_meta.return_value = {"summary": "s"}
        mock_cat.return_value = {
            "suggested_domain": "finance",  # tempting, but MUST be ignored
            "sub_category": "decisions", "tags": ["planning"],
        }
        _mock_stores(mock_chroma, mock_neo4j, mock_redis)
        _prep_graph(mock_graph)

        from app.services.ingestion import ingest_content

        ingest_content("we decided to ship friday", domain="conversations")

        kw = _create_artifact_kwargs(mock_graph)
        assert kw is not None
        assert kw["domain"] == "conversations"  # never moved
        assert kw["sub_category"] == "decisions"

    def test_caller_supplied_tags_suppress_enrichment(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        mock_meta.return_value = {"summary": "s"}
        mock_cat.return_value = {"sub_category": "trading", "tags": ["auto"]}
        _mock_stores(mock_chroma, mock_neo4j, mock_redis)
        _prep_graph(mock_graph)

        from app.services.ingestion import ingest_content

        ingest_content(
            "already tagged",
            domain="general",
            metadata={"tags_json": json.dumps(["manual-tag"]), "sub_category": "preset"},
        )

        # Both fields already present → classifier not consulted for enrichment.
        mock_cat.assert_not_called()
        kw = _create_artifact_kwargs(mock_graph)
        assert kw is not None
        assert json.loads(kw["tags_json"]) == ["manual-tag"]
        assert kw["sub_category"] == "preset"

    def test_classifier_failure_proceeds_untagged(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        mock_meta.return_value = {"summary": "s"}
        mock_cat.side_effect = RuntimeError("classifier down")
        _mock_stores(mock_chroma, mock_neo4j, mock_redis)
        _prep_graph(mock_graph)

        from app.services.ingestion import ingest_content

        # Must not raise — enrichment is best-effort.
        result = ingest_content("some content", domain="general")
        assert result is not None
        kw = _create_artifact_kwargs(mock_graph)
        assert kw is not None  # ingest completed despite classifier failure


@pytest.mark.asyncio
async def test_triage_endpoint_passes_enrich_false():
    """The /agent/triage path must call ingest_content(enrich=False) since
    triage already classified."""
    import app.routers.agents as agents_mod

    captured: dict = {}

    def _fake_ingest(*args, **kwargs):
        captured.update(kwargs)
        return {"status": "ok", "artifact_id": "a1"}

    triage_payload = {
        "status": "ok", "parsed_text": "x", "domain": "coding",
        "metadata": {"filename": "f.py"}, "filename": "f.py",
    }

    with (
        patch.object(agents_mod, "ingest_content", _fake_ingest),
        patch("app.agents.triage.triage_file", new_callable=AsyncMock, return_value=triage_payload),
        patch.object(agents_mod, "validate_file_path"),
    ):
        from app.routers.agents import TriageFileRequest, triage_file_endpoint
        await triage_file_endpoint(TriageFileRequest(file_path="/tmp/f.py"))

    assert captured.get("enrich") is False
