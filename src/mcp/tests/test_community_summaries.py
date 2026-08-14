# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
    _clean_name,
    _disambiguate_name,
    _parse_name_summary,
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

        name, summary = await _summarise(
            entities=[
                {"name": "Federal Reserve", "entity_type": "ORG"},
                {"name": "BTC", "entity_type": "ASSET"},
            ],
            passages=["passage one about money policy", "passage two about cryptocurrency"],
            llm_caller=caller,
        )
        assert summary == "summary text"
        assert name == "Summary text"  # derived fallback: no Name: line given
        assert len(captured) == 1
        prompt = captured[0][-1]["content"]
        assert "Federal Reserve (ORG)" in prompt
        assert "BTC (ASSET)" in prompt
        assert "passage one about money policy" in prompt
        assert "passage two about cryptocurrency" in prompt

    async def test_strips_whitespace_around_summary(self):
        _name, summary = await _summarise(
            entities=[{"name": "X", "entity_type": "ORG"}],
            passages=["snippet"],
            llm_caller=_llm_caller_returning("   the theme   \n\n"),
        )
        assert summary == "the theme"

    async def test_two_line_reply_parses_name_and_summary(self):
        name, summary = await _summarise(
            entities=[{"name": "Spark", "entity_type": "ASSET"}],
            passages=["snippet"],
            llm_caller=_llm_caller_returning(
                "Name: Apache Spark Streaming\n"
                "Summary: Distributed stream processing with Spark."
            ),
        )
        assert name == "Apache Spark Streaming"
        assert summary == "Distributed stream processing with Spark."


# ---------------------------------------------------------------------------
# _parse_name_summary / _clean_name / _disambiguate_name — UX-15
# ---------------------------------------------------------------------------


class TestNameParsing:
    def test_boilerplate_opener_stripped_from_name(self):
        # Both participle ("related to") and present ("relates to") forms.
        assert _clean_name("Content related to database management") == (
            "Database management"
        )
        assert _clean_name("This community revolves around Apache Spark") == (
            "Apache Spark"
        )

    def test_numeric_name_rejected(self):
        assert _clean_name("0.7143") == ""

    def test_name_capped_at_word_boundary(self):
        long = "One Two Three Four Five Six Seven Eight Nine Ten"
        capped = _clean_name(long)
        assert len(capped.split()) <= 8
        assert not capped.endswith(" ")

    def test_summary_only_blob_still_yields_summary(self):
        name, summary = _parse_name_summary(
            "The theme revolves around container orchestration tooling."
        )
        assert "container orchestration" in summary
        assert name.startswith("Container orchestration")

    def test_collision_gets_entity_suffix(self):
        used = {"apache spark"}
        out = _disambiguate_name(
            "Apache Spark", used, [{"name": "SparkContext"}],
        )
        assert out == "Apache Spark (SparkContext)"

    def test_no_collision_passthrough(self):
        assert _disambiguate_name("Helm", set(), [{"name": "x"}]) == "Helm"


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


def _driver_with_named_communities(
    targets: list[dict], named: dict[str, str],
) -> tuple[MagicMock, list[dict]]:
    """Driver that answers the target, name-lookup and persist queries
    separately, honouring ``exclude_id`` on the name lookup.

    ``named`` maps community id -> already-persisted name (i.e. what an
    earlier generation run wrote). Returns the driver plus the list that
    collects persisted rows.
    """
    persisted: list[dict] = []
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value

    def _run(cypher, **params):
        if "c.name IS NOT NULL" in cypher:
            exclude = params.get("exclude_id")
            return [
                {"name": n} for cid, n in named.items() if cid != exclude
            ]
        if "SET c.summary" in cypher:
            persisted.append(params)
            named[params["cid"]] = params["name"]
            return []
        return targets

    session.run.side_effect = _run
    return driver, persisted


@pytest.mark.asyncio
class TestNameCollisionAcrossRuns:
    """A second generation pass must see names the first pass persisted."""

    async def test_name_minted_mid_run_is_still_seen(self):
        """A concurrent pass writes a name after this run began.

        The old behaviour snapshotted taken names once at run start, so a
        name minted by another pass mid-run was invisible and both passes
        shipped the same label.
        """
        targets = [
            {"community_id": "0:8", "level": 0,
             "entities": [{"name": "HelmChart", "entity_type": "ASSET",
                           "degree": 2}]},
            {"community_id": "0:9", "level": 0,
             "entities": [{"name": "SparkContext", "entity_type": "ASSET",
                           "degree": 3}]},
        ]
        named: dict[str, str] = {}
        driver, persisted = _driver_with_named_communities(targets, named)
        chroma = _chroma_returning({
            "HelmChart": "chart templating",
            "SparkContext": "streaming internals",
        })

        calls = {"n": 0}

        async def caller(messages):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] == 1:
                # Another generation pass lands "Apache Spark" while this
                # run is between communities.
                named["0:1"] = "Apache Spark"
                return "Name: Helm Charts\nSummary: Chart templating."
            return "Name: Apache Spark\nSummary: Stream processing internals."

        out = await summarize_communities(driver, chroma, llm_caller=caller)
        assert out["summarised"] == 2
        assert persisted[1]["name"] == "Apache Spark (SparkContext)"

    async def test_resummarising_does_not_collide_with_its_own_name(self):
        targets = [{
            "community_id": "0:1",
            "level": 0,
            "entities": [{"name": "SparkContext", "entity_type": "ASSET",
                          "degree": 3}],
        }]
        # The community being re-summarised already carries this name.
        driver, persisted = _driver_with_named_communities(
            targets, {"0:1": "Apache Spark"},
        )
        chroma = _chroma_returning({"SparkContext": "streaming internals"})
        await summarize_communities(
            driver, chroma,
            llm_caller=_llm_caller_returning(
                "Name: Apache Spark\nSummary: Stream processing internals.",
            ),
        )
        assert persisted[0]["name"] == "Apache Spark"


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
