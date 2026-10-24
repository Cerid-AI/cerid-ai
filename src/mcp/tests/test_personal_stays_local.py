# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""General questions check freshness on the web; personal ones stay local (ruled 2026-10-10).

Live probe: "What is the latest stable release of Python?" was answered from a
2023 fact, because "latest" was not a freshness signal. Making it one must not
send "what is my current balance" to the web, and when a weak KB does send a
personal question out, only a related public fact may leave, with no private
datapoint in it. The local model and the external registry are faked; the
gate, the rewrite rules and the CRAG path are real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from core.agents import crag
from core.agents.external_query import external_query_for, freshness_intent

STALE = (datetime.now(timezone.utc) - timedelta(days=125)).isoformat()


@pytest.mark.parametrize(("query", "days"), [
    ("What is the latest stable release of Python?", 7),
    ("current population of Tokyo", 7),
    ("What happened in AI news this week?", 7),
    ("what is my current 401k balance", None),
    ("what did we decide this week about the garden", None),
    ("how does photosynthesis work", None),
])
def test_freshness_is_asked_of_general_questions_only(query, days):
    assert freshness_intent(query) == days


def _strong_but_stale() -> dict:
    row = {"content": "Python 3.12 was released in October 2023", "relevance": 0.95,
           "rerank_score": 0.97, "artifact_id": "a1", "ingested_at": STALE}
    return {"results": [row]}


def test_a_stale_answer_to_a_latest_question_goes_to_the_web():
    assert crag.crag_wants_external(_strong_but_stale(), "What is the latest stable release of Python?", 0.6)


def test_a_stale_answer_to_a_personal_current_question_stays_local():
    assert not crag.crag_wants_external(_strong_but_stale(), "what is my current Python version at work", 0.6)


@pytest.mark.asyncio
async def test_a_general_question_goes_out_as_asked():
    assert await external_query_for("latest stable release of Python") == "latest stable release of Python"


@pytest.mark.asyncio
@pytest.mark.parametrize(("rewrite", "sent"), [
    ("VTSAX share price", "VTSAX share price"),
    ('"VTSAX price for my $12,400 balance, account 4417-0032"', "VTSAX price for balance, account"),
    ("call 415 555 0199 about the VTSAX fee", "call about the VTSAX fee"),
    ("12,400 USD in VTSAX: current price", "in VTSAX: current price"),
    ("NONE", None),
    ("", None),
])
async def test_a_personal_question_goes_out_only_as_a_public_fact(rewrite, sent):
    with patch("core.utils.internal_llm.call_internal_llm", new=AsyncMock(return_value=rewrite)) as llm:
        out = await external_query_for("what is my current VTSAX balance in account 4417-0032?")

    assert out == sent
    assert llm.await_args.kwargs["stage"] == "external_query_generalize"


@pytest.mark.asyncio
async def test_no_local_rewrite_means_nothing_goes_out():
    with patch("core.utils.internal_llm.call_internal_llm",
               new=AsyncMock(side_effect=RuntimeError("runs only on a local provider"))):
        assert await external_query_for("how much do I owe on my car") is None


def test_the_rewrite_never_leaves_the_machine():
    from config.stage_profiles import PRIVATE_LOCAL_STAGES

    assert "external_query_generalize" in PRIVATE_LOCAL_STAGES


@pytest.mark.asyncio
@pytest.mark.parametrize(("rewrite", "queried"), [("VTSAX share price", True), ("NONE", False)])
async def test_crag_sends_only_the_rewrite(rewrite, queried):
    registry = AsyncMock()
    registry.query_all = AsyncMock(return_value=[])
    personal = "what is my VTSAX balance worth today in account 4417-0032"
    with (
        patch.object(crag, "_external_registry", registry),
        patch.object(crag, "_search_term_extractor", lambda q: q),
        patch("core.utils.internal_llm.call_internal_llm", new=AsyncMock(return_value=rewrite)),
    ):
        await crag.augment_external_crag({"results": []}, personal, None, 0.6)

    if queried:
        assert registry.query_all.await_args.args[0] == "VTSAX share price"
    else:
        registry.query_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_smart_mode_sends_only_the_rewrite_as_the_raw_query():
    from app.agents.retrieval_orchestrator import _query_external_sources

    with (
        patch("app.data_sources.registry.query_all", new=AsyncMock(return_value=[])) as query_all,
        patch("core.utils.internal_llm.call_internal_llm", new=AsyncMock(return_value="VTSAX share price")),
    ):
        await _query_external_sources("what is my VTSAX balance in account 4417-0032")

    assert query_all.await_args.kwargs["raw_query"] == "VTSAX share price"
    assert "4417" not in query_all.await_args.args[0]


@pytest.mark.parametrize(("query", "personal"), [
    ("what is my current 401k balance", True),
    ("balance of account 4417-0032", True),
    ("draft a reply to sarah@acme.com about the invoice", True),
    ("is $12,400 enough for the down payment", True),
    ("call 415 555 0199 back", True),
    ("born 01/02/1990, when can I retire", True),
    ("forward this to sarah at acme dot com", True),
    ("tell me the latest stable release of Python", False),
    ("can you explain the Python 3.12 release notes", False),
    ("what changed in the 2024 tax brackets", False),
    ("best 4k monitor for photo editing", False),
    # A name is not private (ruled 2026-10-11); only PII is.
    ("what did Sarah Chen say about the Acme merger", False),
])
def test_a_question_is_personal_by_its_words_or_its_datapoints(query, personal):
    from core.agents.external_query import is_personal_query

    assert is_personal_query(query) is personal


def test_a_question_addressed_to_the_assistant_still_asks_about_freshness():
    assert freshness_intent("tell me the latest stable release of Python") == 7


@pytest.mark.parametrize("rewrite", [
    "VTSAX price per share",
    "call 415 555 0199 about the VTSAX fee",
    "email sarah@acme.com the VTSAX fund page",
    "12,400 dollars of VTSAX",
])
def test_nothing_the_detector_flags_survives_the_rewrite(rewrite):
    """The detector and the cleaner disagreed: a spaced phone number marked a
    question personal but survived the cleaner, so the rewrite could carry it."""
    from core.agents.external_query import _clean, is_personal_query

    out = _clean(rewrite)
    assert out and not is_personal_query(out)


@pytest.mark.parametrize("text", [
    "transfer to acct.44170032",
    "balance of account 4417.0032",
    "appointment on 01.02.1990",
    "call 415.555.0199 back",
])
def test_dotted_account_numbers_and_dates_are_private(text):
    """A looser pattern that let decimals through also let these out."""
    from core.agents.external_query import is_personal_query

    assert is_personal_query(text)


@pytest.mark.asyncio
async def test_a_general_claim_with_a_decimal_still_reaches_the_web_as_a_public_fact():
    """'180.16 g/mol' reads as a datapoint; the cost is a local rewrite, not silence."""
    from core.agents.external_query import external_query_for

    with patch("core.utils.internal_llm.call_internal_llm",
               new=AsyncMock(return_value="aspirin molecular weight 180.16")):
        out = await external_query_for("Aspirin has a molecular weight of 180.16 g/mol")

    assert out == "aspirin molecular weight"


def _rows(*specs) -> dict:
    return {"results": [
        {"content": c, "relevance": 0.9, "rerank_score": ce, "artifact_id": c,
         "ingested_at": (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()}
        for c, ce, age in specs
    ]}


def test_freshness_is_judged_on_the_rows_that_answer():
    """Live: a July docs pack answered "the latest stable release of Python"
    while an unrelated row from yesterday made the whole result look fresh."""
    q = "What is the latest stable release of Python?"

    assert crag.crag_wants_external(_rows(("python docs", 0.85, 91), ("unrelated", 0.0, 1)), q, 0.6)
    assert not crag.crag_wants_external(_rows(("python docs", 0.85, 2), ("unrelated", 0.0, 90)), q, 0.6)
