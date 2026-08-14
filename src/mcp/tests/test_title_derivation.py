# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Todo item 6 — derived titles for text_input artifacts.

Artifacts ingested without a filename were all literally named
``text_input``; provenance was unrecoverable and citations useless. The
ingest chokepoint now derives a display title (caller title > first
heading > opening line) and the harness half here proves the wiring:
``create_artifact`` receives the derived name, never the placeholder.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from core.utils.title_derivation import MAX_TITLE_CHARS, derive_title


class TestDeriveTitle:
    def test_prefers_first_markdown_heading(self):
        content = "some preamble text\n\n## Quarterly Planning Notes\n\nbody"
        assert derive_title(content) == "Quarterly Planning Notes"

    def test_falls_back_to_first_prose_line(self):
        content = "Meeting with the vendor about the Q3 rollout.\nMore text."
        assert derive_title(content) == "Meeting with the vendor about the Q3 rollout."

    def test_strips_markdown_emphasis_and_bullets(self):
        assert derive_title("- **Grocery list** for the week") == (
            "Grocery list for the week"
        )

    def test_truncates_at_word_boundary(self):
        long_line = "word " * 40
        title = derive_title(long_line)
        assert len(title) <= MAX_TITLE_CHARS + 1  # +1 for the ellipsis
        assert title.endswith("…")

    def test_skips_code_fences_and_rules(self):
        content = "```\ncode here\n```\n---\nActual first prose line"
        assert derive_title(content) == "Actual first prose line"

    def test_empty_and_markup_only_content_yield_nothing(self):
        assert derive_title("") == ""
        assert derive_title("\n\n```\n```\n---\n") == ""


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


@patch("app.services.ingestion.graph")
@patch("app.services.ingestion.get_redis")
@patch("app.services.ingestion.get_neo4j")
@patch("app.services.ingestion.get_chroma")
@patch("app.services.ingestion.extract_metadata", new_callable=AsyncMock)
@patch("app.services.ingestion.ai_categorize", new_callable=AsyncMock)
class TestIngestDerivesTitles:
    @staticmethod
    def _ingest(content, metadata=None):
        from app.services.ingestion import ingest_content

        return ingest_content(content, domain="general", metadata=metadata)

    @staticmethod
    def _created_filename(mock_graph):
        assert mock_graph.create_artifact.called, "create_artifact was not reached"
        return mock_graph.create_artifact.call_args.kwargs["filename"]

    def _prime(self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis,
               mock_graph):
        mock_meta.return_value = {"summary": "s"}
        mock_cat.return_value = {}
        _mock_stores(mock_chroma, mock_neo4j, mock_redis)
        mock_graph.discover_relationships.return_value = 0

    def test_missing_filename_gets_content_derived_title(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        self._prime(mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis,
                    mock_graph)
        self._ingest("# Rollout checklist\n\n1. freeze\n2. tag")
        assert self._created_filename(mock_graph) == "Rollout checklist"

    def test_caller_title_wins_over_content(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        self._prime(mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis,
                    mock_graph)
        self._ingest(
            "body text of the mail message here",
            metadata={"title": "Order shipped: Tiger's Eye bracelet"},
        )
        assert self._created_filename(mock_graph) == (
            "Order shipped: Tiger's Eye bracelet"
        )

    def test_explicit_filename_is_untouched(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        self._prime(mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis,
                    mock_graph)
        self._ingest("# A heading", metadata={"filename": "notes.md"})
        assert self._created_filename(mock_graph) == "notes.md"

    def test_underivable_content_keeps_the_placeholder(
        self, mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis, mock_graph,
    ):
        self._prime(mock_cat, mock_meta, mock_chroma, mock_neo4j, mock_redis,
                    mock_graph)
        self._ingest("```\nxx\n```")
        assert self._created_filename(mock_graph) == "text_input"
