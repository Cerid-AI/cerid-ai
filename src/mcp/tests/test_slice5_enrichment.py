# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Slice 5.1 + 5.2 — ingestion enrichment seam + classifier accuracy.

5.1 — ``ingest_content`` auto-classifies sub_category + tags via
``ai_categorize`` when the caller supplied neither (opt-out via
``enrich=False``); NEVER changes domain (conversations carve-out is then
automatic); classifier failure proceeds untagged.

5.2 — ``ai_categorize`` samples head+middle+tail of the document, reports a
confidence that demotes low-confidence domains to general + ``needs-review``,
and accepts the new ``trading`` (finance) / ``career`` (personal)
sub_categories.
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
        assert kw["domain"] == "general"  # never moved

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
        """HARD CONSTRAINT — conversations is never re-domained."""
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

        result = ingest_content("some content", domain="general")  # must not raise
        assert result is not None
        kw = _create_artifact_kwargs(mock_graph)
        assert kw is not None


@pytest.mark.asyncio
async def test_triage_endpoint_passes_enrich_false():
    """The /agent/triage path must call ingest_content(enrich=False)."""
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


# ---------------------------------------------------------------------------
# Slice 5.2 — classifier accuracy
# ---------------------------------------------------------------------------


def test_sample_short_text_returned_whole():
    from utils.metadata import _sample_for_classification

    assert _sample_for_classification("short doc", 1500) == "short doc"


def test_sample_long_text_head_middle_tail():
    from utils.metadata import _sample_for_classification

    text = "H" * 1000 + "M" * 1000 + "T" * 1000  # 3000 chars
    out = _sample_for_classification(text, 600)
    assert "H" in out and "M" in out and "T" in out
    assert out.count("[... elided ...]") == 2


def _llm_json(payload: dict) -> str:
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_low_confidence_demotes_to_general_with_needs_review():
    import config
    from utils import metadata

    payload = {
        "domain": "finance", "sub_category": "trading",
        "confidence": 0.3, "tags": ["earnings"], "keywords": [], "summary": "s",
    }
    with (
        patch.object(config, "INTERNAL_LLM_PROVIDER", "openrouter"),
        patch("core.utils.llm_client.call_llm", new_callable=AsyncMock, return_value=_llm_json(payload)),
    ):
        out = await metadata.ai_categorize("ambiguous text", "doc.txt", mode="pro")

    assert out["suggested_domain"] == "general"  # demoted
    assert "needs-review" in out["tags"]
    assert out["confidence"] == 0.3


@pytest.mark.asyncio
async def test_high_confidence_preserves_domain_and_trading_subcategory():
    import config
    from utils import metadata

    payload = {
        "domain": "finance", "sub_category": "trading",
        "confidence": 0.9, "tags": ["signals"], "keywords": [], "summary": "s",
    }
    with (
        patch.object(config, "INTERNAL_LLM_PROVIDER", "openrouter"),
        patch("core.utils.llm_client.call_llm", new_callable=AsyncMock, return_value=_llm_json(payload)),
    ):
        out = await metadata.ai_categorize("a trade signal log", "signals.csv", mode="pro")

    assert out["suggested_domain"] == "finance"
    assert out["sub_category"] == "trading"  # 5.2 new sub_category accepted
    assert "needs-review" not in out["tags"]


@pytest.mark.asyncio
async def test_career_subcategory_accepted_under_personal():
    import config
    from utils import metadata

    payload = {
        "domain": "personal", "sub_category": "career",
        "confidence": 0.8, "tags": ["resume"], "keywords": [], "summary": "s",
    }
    with (
        patch.object(config, "INTERNAL_LLM_PROVIDER", "openrouter"),
        patch("core.utils.llm_client.call_llm", new_callable=AsyncMock, return_value=_llm_json(payload)),
    ):
        out = await metadata.ai_categorize("my resume", "resume.pdf", mode="pro")

    assert out["suggested_domain"] == "personal"
    assert out["sub_category"] == "career"  # 5.2 new sub_category accepted


@pytest.mark.asyncio
async def test_missing_confidence_defaults_high_no_demotion():
    """A model that omits confidence must not be silently demoted."""
    import config
    from utils import metadata

    payload = {
        "domain": "finance", "sub_category": "tax",
        "tags": ["w2"], "keywords": [], "summary": "s",  # no confidence key
    }
    with (
        patch.object(config, "INTERNAL_LLM_PROVIDER", "openrouter"),
        patch("core.utils.llm_client.call_llm", new_callable=AsyncMock, return_value=_llm_json(payload)),
    ):
        out = await metadata.ai_categorize("a tax form", "w2.pdf", mode="pro")

    assert out["suggested_domain"] == "finance"
    assert "needs-review" not in out["tags"]
