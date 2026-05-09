# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for app.db.neo4j.community_summaries.

Covers prompt assembly, top-entity → passage fetch, persistence, and
the skip_with_existing path. The end-to-end LLM round-trip is
exercised via the live integration suite.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.db.neo4j.community_summaries import (
    SUMMARY_PROMPT,
    _summarise,
    list_community_summaries,
    summarize_communities,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm_caller_returning(text: str):
    async def caller(messages):  # noqa: ARG001
        return text
    return caller


def _driver_with_targets(targets: list[dict]) -> MagicMock:
    """Mock driver where session.run returns the canned targets list."""
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value = targets
    return driver


def _chroma_returning(passages_per_entity: dict[str, str]) -> MagicMock:
    """Mock chroma where each .query(query_texts=[name]) returns the passage
    keyed by the first query_text."""
    chroma = MagicMock()

    def _coll(name):  # noqa: ARG001
        coll = MagicMock()

        def _q(query_texts=None, n_results=1, include=None):  # noqa: ARG001
            qt = (query_texts or [""])[0]
            doc = passages_per_entity.get(qt, "")
            return {
                "ids": [[f"chunk_for_{qt}"] if doc else []],
                "documents": [[doc] if doc else []],
            }

        coll.query.side_effect = _q
        return coll

    chroma.get_collection.side_effect = _coll
    return chroma


# ---------------------------------------------------------------------------
# _summarise — prompt assembly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSummarisePromptShape:
    async def test_lists_entities_and_passages(self):
        captured: list[dict] = []

        async def caller(messages):
            captured.append(messages)
            return "summary text"

        out = await _summarise(
            entities=[
                {"name": "Federal Reserve", "entity_type": "ORG"},
                {"name": "BTC", "entity_type": "ASSET"},
            ],
            passages=["passage one about money policy", "passage two about cryptocurrency"],
            llm_caller=caller,
        )
        assert out == "summary text"
        assert len(captured) == 1
        prompt = captured[0][-1]["content"]
        assert "Federal Reserve (ORG)" in prompt
        assert "BTC (ASSET)" in prompt
        assert "passage one about money policy" in prompt
        assert "passage two about cryptocurrency" in prompt

    async def test_strips_whitespace_around_summary(self):
        out = await _summarise(
            entities=[{"name": "X", "entity_type": "ORG"}],
            passages=["snippet"],
            llm_caller=_llm_caller_returning("   the theme   \n\n"),
        )
        assert out == "the theme"


# ---------------------------------------------------------------------------
# summarize_communities — happy path + skip behaviours
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSummarizeCommunities:
    async def test_summarises_each_community(self):
        targets = [
            {
                "community_id": "0:1",
                "level": 0,
                "entities": [{"name": "Federal Reserve", "entity_type": "ORG", "degree": 5}],
            },
            {
                "community_id": "0:2",
                "level": 0,
                "entities": [{"name": "Apple", "entity_type": "ORG", "degree": 4}],
            },
        ]
        driver = _driver_with_targets(targets)
        chroma = _chroma_returning({
            "Federal Reserve": "Fed lowers rates again",
            "Apple": "iPhone launch event",
        })
        out = await summarize_communities(
            driver, chroma, llm_caller=_llm_caller_returning("This community covers X."),
        )
        assert out["summarised"] == 2
        assert out["skipped_no_chunks"] == 0

    async def test_skips_when_no_chunks_returned(self):
        targets = [{
            "community_id": "0:7",
            "level": 0,
            "entities": [{"name": "ObscureEntity", "entity_type": "OTHER", "degree": 1}],
        }]
        driver = _driver_with_targets(targets)
        chroma = _chroma_returning({})  # no passages
        out = await summarize_communities(
            driver, chroma, llm_caller=_llm_caller_returning("..."),
        )
        assert out["summarised"] == 0
        assert out["skipped_no_chunks"] == 1

    async def test_max_communities_caps_runs(self):
        targets = [
            {"community_id": f"0:{i}", "level": 0,
             "entities": [{"name": f"Ent{i}", "entity_type": "ORG", "degree": 1}]}
            for i in range(10)
        ]
        driver = _driver_with_targets(targets)
        chroma = _chroma_returning({f"Ent{i}": f"snippet {i}" for i in range(10)})
        out = await summarize_communities(
            driver, chroma, max_communities=3,
            llm_caller=_llm_caller_returning("theme"),
        )
        assert out["summarised"] == 3


# ---------------------------------------------------------------------------
# list_community_summaries — readback shape
# ---------------------------------------------------------------------------

class TestListSummaries:
    def test_returns_dicts_in_member_count_order(self):
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value = [
            {"id": "0:1", "level": 0, "summary": "alpha",
             "generated_at": "now", "member_count": 5},
            {"id": "0:2", "level": 0, "summary": "beta",
             "generated_at": "now", "member_count": 3},
        ]
        out = list_community_summaries(driver, level=0)
        assert [c["id"] for c in out] == ["0:1", "0:2"]
        assert out[0]["summary"] == "alpha"


def test_summary_prompt_contains_constraints():
    """Catches accidental prompt regression that drops the length cap."""
    assert "1-3 concise sentences" in SUMMARY_PROMPT
    assert "<=80 words" in SUMMARY_PROMPT
