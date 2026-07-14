# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the freshness model (Phase 3.4, 2026-07-13 quality-maximization
program) — claim freshness classification, evidence recency weighting, and
the verdict-cache TTL cap that applies regardless of verification method.

Pins two contracts the plan calls out explicitly:

- A claim classified ``time_sensitive`` gets the 3-day cache cap EVEN WHEN
  it resolves via a non-web method (kb / kb_nli / cross_model). Before this
  change, only ``verification_method == "web_search"`` verdicts were capped
  (see ``TestVerifyClaimCachesTimeSensitiveWithCappedTtl`` — the first test
  there fails against the pre-Phase-3.4 code).
- For a time-sensitive claim, evidence selection among near-equally-relevant
  KB results prefers the newer source (bounded — see
  ``TestVerifyClaimPrefersNewerEvidence``).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from core.agents.hallucination.freshness import (
    ClaimFreshness,
    classify_claim_freshness,
    evidence_age_days,
    extract_year_from_text,
    weight_evidence_by_recency,
)
from core.agents.hallucination.verification import verify_claim
from core.utils.claim_cache import TIME_SENSITIVE_VERDICT_TTL_S
from core.utils.time import utcnow


def _iso_days_ago(days: float) -> str:
    return (utcnow() - timedelta(days=days)).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# classify_claim_freshness
# ---------------------------------------------------------------------------


class TestClassifyClaimFreshness:
    def test_price_claim_is_time_sensitive(self):
        assert classify_claim_freshness(
            "The gym membership subscription costs $45 per month"
        ) == ClaimFreshness.TIME_SENSITIVE

    def test_current_leadership_claim_is_time_sensitive(self):
        assert classify_claim_freshness(
            "The current CEO of Acme Corp is Jane Doe"
        ) == ClaimFreshness.TIME_SENSITIVE

    def test_latest_version_claim_is_time_sensitive(self):
        assert classify_claim_freshness(
            "The latest release of the app is version 4.2"
        ) == ClaimFreshness.TIME_SENSITIVE

    def test_world_record_claim_is_time_sensitive(self):
        assert classify_claim_freshness(
            "The current world record for the 100m sprint is 9.58 seconds"
        ) == ClaimFreshness.TIME_SENSITIVE

    def test_scheduled_event_claim_is_time_sensitive(self):
        assert classify_claim_freshness(
            "The next scheduled launch window opens in March"
        ) == ClaimFreshness.TIME_SENSITIVE

    def test_reuses_existing_current_event_signal(self):
        """No phrase-list match here — relies on the broader, reused
        ``_is_current_event_claim`` heuristic (2+ weak temporal signals)."""
        assert classify_claim_freshness(
            "The 2026 election will determine the next mayor of the city"
        ) == ClaimFreshness.TIME_SENSITIVE

    def test_explicit_year_without_volatility_language_is_dated(self):
        assert classify_claim_freshness(
            "The Berlin Wall fell in 1989"
        ) == ClaimFreshness.DATED

    def test_release_year_without_currency_language_is_dated(self):
        assert classify_claim_freshness(
            "Python 3.8 was released in 2019"
        ) == ClaimFreshness.DATED

    def test_definitional_claim_is_timeless(self):
        assert classify_claim_freshness(
            "Water boils at 100 degrees Celsius at sea level"
        ) == ClaimFreshness.TIMELESS

    def test_mathematical_claim_is_timeless(self):
        assert classify_claim_freshness(
            "A triangle has three interior angles that sum to 180 degrees"
        ) == ClaimFreshness.TIMELESS

    def test_no_temporal_signal_defaults_to_timeless(self):
        assert classify_claim_freshness(
            "Paris is the capital of France"
        ) == ClaimFreshness.TIMELESS

    def test_date_plus_volatility_language_defaults_time_sensitive(self):
        """Ambiguous: both a fixed date and 'current record' framing. The
        safe default is TIME_SENSITIVE — capping the cache TTL too loosely
        risks serving a stale volatile fact for up to 30 days, while capping
        it too tightly just costs one extra re-verification."""
        assert classify_claim_freshness(
            "As of 2023, the current record holder is Team Alpha"
        ) == ClaimFreshness.TIME_SENSITIVE


# ---------------------------------------------------------------------------
# extract_year_from_text
# ---------------------------------------------------------------------------


class TestExtractYearFromText:
    def test_extracts_most_recent_plausible_year(self):
        assert extract_year_from_text("Published in 2021, revised 2022.") == 2022

    def test_ignores_non_year_numbers(self):
        assert extract_year_from_text("There were 42 participants and 100 votes.") is None

    def test_ignores_implausible_years(self):
        assert extract_year_from_text("Model number 9999 was recalled.") is None

    def test_no_digits_returns_none(self):
        assert extract_year_from_text("No dates mentioned here at all.") is None


# ---------------------------------------------------------------------------
# evidence_age_days
# ---------------------------------------------------------------------------


class TestEvidenceAgeDays:
    def test_prefers_created_at_metadata(self):
        age = evidence_age_days({"created_at": _iso_days_ago(5), "content": "mentions 1999 nowhere relevant"})
        assert age is not None
        assert abs(age - 5) < 1

    def test_falls_back_to_ingested_at(self):
        age = evidence_age_days({"ingested_at": _iso_days_ago(9)})
        assert age is not None
        assert abs(age - 9) < 1

    def test_falls_back_to_text_year_when_no_metadata(self):
        ev = {"content": "The report, published in 2020, covers Q4 results."}
        age = evidence_age_days(ev)
        assert age is not None
        expected = (utcnow().replace(tzinfo=None) - _iso_year_start(2020)).total_seconds() / 86400.0
        assert abs(age - expected) < 2

    def test_no_date_signal_returns_none(self):
        assert evidence_age_days({"content": "no dates in here at all"}) is None


def _iso_year_start(year: int):
    from datetime import datetime
    return datetime(year, 1, 1)


# ---------------------------------------------------------------------------
# weight_evidence_by_recency
# ---------------------------------------------------------------------------


class TestWeightEvidenceByRecency:
    def test_noop_for_timeless_claims(self):
        results = [
            {"relevance": 0.6, "created_at": _iso_days_ago(1)},
            {"relevance": 0.5, "created_at": _iso_days_ago(3000)},
        ]
        out = weight_evidence_by_recency(results, ClaimFreshness.TIMELESS)
        assert out is results

    def test_noop_for_dated_claims(self):
        results = [
            {"relevance": 0.6, "created_at": _iso_days_ago(1)},
            {"relevance": 0.5, "created_at": _iso_days_ago(3000)},
        ]
        out = weight_evidence_by_recency(results, ClaimFreshness.DATED)
        assert out is results

    def test_noop_for_single_result(self):
        results = [{"relevance": 0.5, "created_at": _iso_days_ago(3000)}]
        out = weight_evidence_by_recency(results, ClaimFreshness.TIME_SENSITIVE)
        assert out is results

    def test_prefers_newer_evidence_on_near_tie(self):
        older = {"relevance": 0.62, "created_at": _iso_days_ago(3000)}
        newer = {"relevance": 0.60, "created_at": _iso_days_ago(2)}
        out = weight_evidence_by_recency([older, newer], ClaimFreshness.TIME_SENSITIVE)
        assert out[0] is newer, (
            "near-tie evidence should prefer the fresher source for a "
            "time-sensitive claim"
        )

    def test_bounded_bonus_never_overrides_much_stronger_older_match(self):
        strong_old = {"relevance": 0.90, "created_at": _iso_days_ago(3000)}
        weak_new = {"relevance": 0.30, "created_at": _iso_days_ago(0)}
        out = weight_evidence_by_recency([strong_old, weak_new], ClaimFreshness.TIME_SENSITIVE)
        assert out[0] is strong_old, (
            "recency bonus must be bounded — cannot flip a large relevance gap"
        )

    def test_missing_date_gets_no_bonus_or_penalty(self):
        dated = {"relevance": 0.55, "created_at": _iso_days_ago(1)}
        undated = {"relevance": 0.56, "created_at": None, "content": "no date info here"}
        out = weight_evidence_by_recency([dated, undated], ClaimFreshness.TIME_SENSITIVE)
        # `undated` keeps its raw relevance (0.56); `dated` gets a near-full
        # bonus at 1 day old (0.55 + ~0.05), so it edges ahead.
        assert out[0] is dated


# ---------------------------------------------------------------------------
# verify_claim integration — TTL cap regardless of verification method
# ---------------------------------------------------------------------------


def _kb_result(*, relevance: float, artifact_id: str, created_days_ago: float, content: str) -> dict:
    return {
        "relevance": relevance,
        "artifact_id": artifact_id,
        "filename": "notes.md",
        "domain": "notes",
        "content": content,
        "created_at": _iso_days_ago(created_days_ago),
    }


class TestVerifyClaimCachesTimeSensitiveWithCappedTtl:
    """Phase 3.4 — the cache-TTL cap applies to the CLAIM's freshness class,
    not just to verdicts produced by the web_search method.

    This claim ("the latest release ... is version 4.2") is time_sensitive
    under the new ``classify_claim_freshness`` (matches the "latest release"
    phrase) but is NOT flagged by the pre-existing ``_is_current_event_claim``
    / ``_is_recency_claim`` gates verify_claim already used to force a web
    escalation — so it resolves entirely via the KB path
    (``verification_method == "kb_nli"``). Pre-Phase-3.4, this write went out
    at the 30-day default TTL; that is exactly the gap the plan calls out
    ("today only web_search-method verdicts get the short TTL").
    """

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification.get_cached_verdict", new_callable=AsyncMock, return_value=None)
    @patch("core.utils.nli.nli_score_async", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_time_sensitive_claim_via_kb_method_gets_capped_ttl(
        self, mock_kb, _mem, mock_nli, _getcache, mock_chroma, mock_neo4j, mock_redis,
    ):
        claim = "The latest release of the app is version 4.2"
        mock_kb.return_value = [
            _kb_result(
                relevance=0.9, artifact_id="art-1", created_days_ago=1,
                content="The latest release of the app is version 4.2, shipped this week.",
            )
        ]
        mock_nli.return_value = {
            "entailment": 0.95, "contradiction": 0.0, "neutral": 0.05, "label": "entailment",
        }

        result = await verify_claim(claim, mock_chroma[0], None, mock_redis)

        assert result["status"] == "verified"
        assert result["verification_method"] == "kb_nli"
        mock_redis.set.assert_called_once()
        ttl = mock_redis.set.call_args[0][2]
        assert ttl == TIME_SENSITIVE_VERDICT_TTL_S, (
            f"time-sensitive claim resolved via '{result['verification_method']}' "
            f"(non-web) cached with ttl={ttl}s — must be capped to "
            f"{TIME_SENSITIVE_VERDICT_TTL_S}s regardless of verification method"
        )

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification.get_cached_verdict", new_callable=AsyncMock, return_value=None)
    @patch("core.utils.nli.nli_score_async", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_timeless_claim_keeps_default_ttl(
        self, mock_kb, _mem, mock_nli, _getcache, mock_chroma, mock_neo4j, mock_redis,
    ):
        """Control: a genuinely timeless claim is unaffected — the cap must
        not over-apply to every KB-resolved verdict."""
        claim = "The Eiffel Tower is 330 meters tall"
        mock_kb.return_value = [
            _kb_result(
                relevance=0.9, artifact_id="art-2", created_days_ago=1,
                content="The Eiffel Tower is 330 meters tall, a Paris landmark.",
            )
        ]
        mock_nli.return_value = {
            "entailment": 0.95, "contradiction": 0.0, "neutral": 0.05, "label": "entailment",
        }

        result = await verify_claim(claim, mock_chroma[0], None, mock_redis)

        assert result["status"] == "verified"
        mock_redis.set.assert_called_once()
        ttl = mock_redis.set.call_args[0][2]
        assert ttl == 2_592_000


class TestVerifyClaimPrefersNewerEvidence:
    """Phase 3.4 — evidence selection prefers newer KB sources for
    time-sensitive claims, bounded so it only breaks near-ties."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification.cache_verdict", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification.get_cached_verdict", new_callable=AsyncMock, return_value=None)
    @patch("core.utils.nli.nli_score_async", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_time_sensitive_claim_prefers_fresher_near_tied_evidence(
        self, mock_kb, _mem, mock_nli, _getcache, _setcache, mock_chroma, mock_neo4j, mock_redis,
    ):
        claim = "The latest release of the app is version 4.2"
        mock_kb.return_value = [
            _kb_result(
                relevance=0.83, artifact_id="art-old", created_days_ago=3000,
                content="The latest release of the app is version 4.2, first shipped years ago.",
            ),
            _kb_result(
                relevance=0.80, artifact_id="art-new", created_days_ago=1,
                content="The latest release of the app is version 4.2, shipped this week.",
            ),
        ]
        mock_nli.return_value = {
            "entailment": 0.95, "contradiction": 0.0, "neutral": 0.05, "label": "entailment",
        }

        result = await verify_claim(claim, mock_chroma[0], None, mock_redis)

        assert result["status"] == "verified"
        assert result["source_artifact_id"] == "art-new", (
            "higher-relevance-but-stale evidence (art-old, rel=0.83) beat "
            "near-tied fresher evidence (art-new, rel=0.80) — recency "
            "weighting did not apply"
        )

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification.cache_verdict", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification.get_cached_verdict", new_callable=AsyncMock, return_value=None)
    @patch("core.utils.nli.nli_score_async", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_timeless_claim_keeps_pure_relevance_order(
        self, mock_kb, _mem, mock_nli, _getcache, _setcache, mock_chroma, mock_neo4j, mock_redis,
    ):
        """Control: recency weighting must not touch a timeless claim, even
        when a much fresher lower-relevance result is available."""
        claim = "The Eiffel Tower is 330 meters tall"
        mock_kb.return_value = [
            _kb_result(
                relevance=0.83, artifact_id="art-old", created_days_ago=3000,
                content="The Eiffel Tower is 330 meters tall, a Paris landmark.",
            ),
            _kb_result(
                relevance=0.80, artifact_id="art-new", created_days_ago=1,
                content="The Eiffel Tower is 330 meters tall, a Paris landmark.",
            ),
        ]
        mock_nli.return_value = {
            "entailment": 0.95, "contradiction": 0.0, "neutral": 0.05, "label": "entailment",
        }

        result = await verify_claim(claim, mock_chroma[0], None, mock_redis)

        assert result["status"] == "verified"
        assert result["source_artifact_id"] == "art-old"
