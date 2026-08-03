# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the shared answer-synthesis primitive (Fix A)."""
from __future__ import annotations

import pytest

from core.agents.answer_synthesis import (
    AnswerMode,
    build_answer_messages,
    classify_answer_mode,
    extract_final_answer,
    suggested_max_tokens,
)


@pytest.mark.parametrize(
    "question,expected",
    [
        ("How many days passed between my MoMA visit and the exhibit?", AnswerMode.TEMPORAL),
        ("How long ago did I meet my aunt?", AnswerMode.TEMPORAL),
        ("When did I start my new job?", AnswerMode.TEMPORAL),
        ("How many model kits have I bought?", AnswerMode.AGGREGATION),
        ("What is the total number of projects I lead?", AnswerMode.AGGREGATION),
        # Frequency questions are NOT aggregation (the answer is the current rate),
        # even though "how often" is inside the aggregation pattern.
        ("How often do I attend yoga classes?", AnswerMode.EXTRACTIVE),
        ("How many times a week do I run?", AnswerMode.EXTRACTIVE),
        ("Can you suggest a hotel for my Miami trip?", AnswerMode.PREFERENCE),
        ("What should I read next?", AnswerMode.PREFERENCE),
        ("What is my cat's name?", AnswerMode.EXTRACTIVE),
        ("Where do I work?", AnswerMode.EXTRACTIVE),
    ],
)
def test_classify_answer_mode_from_text(question, expected) -> None:
    assert classify_answer_mode(question) == expected


def test_temporal_beats_aggregation_on_how_many_days() -> None:
    # "how many days" is a duration, not a count — temporal must win.
    assert classify_answer_mode("How many days between X and Y?") == AnswerMode.TEMPORAL


@pytest.mark.parametrize(
    "qtype,expected",
    [
        ("temporal-reasoning", AnswerMode.TEMPORAL),
        ("multi-session", AnswerMode.AGGREGATION),
        ("single-session-preference", AnswerMode.PREFERENCE),
        ("single-session-assistant", AnswerMode.EXTRACTIVE),
        ("knowledge-update", AnswerMode.EXTRACTIVE),
    ],
)
def test_oracle_question_type_routes(qtype, expected) -> None:
    # Oracle label wins even if the text looks otherwise.
    assert classify_answer_mode("anything", qtype) == expected


def test_extractive_grounding_has_recency_and_dates() -> None:
    user = build_answer_messages("q?", "mem", AnswerMode.EXTRACTIVE)[-1]["content"]
    low = user.lower()
    assert "recorded" in low and "most recent" in low
    assert "i don't know" in low  # extractive keeps the abstention escape


def test_temporal_prompt_demands_derivation_not_abstention() -> None:
    user = build_answer_messages("q?", "mem", AnswerMode.TEMPORAL)[-1]["content"].lower()
    assert "derive" in user
    assert "answer:" in user  # asks for an explicit final-answer line


def test_aggregation_prompt_enumerates_then_counts() -> None:
    user = build_answer_messages("q?", "mem", AnswerMode.AGGREGATION)[-1]["content"].lower()
    assert "exhaustive" in user
    assert "do not abstain" in user


def test_analytical_modes_use_chain_of_note_json() -> None:
    for mode in (AnswerMode.TEMPORAL, AnswerMode.AGGREGATION, AnswerMode.PREFERENCE):
        user = build_answer_messages("q?", "mem", mode)[-1]["content"].lower()
        assert "step 1" in user and "step 2" in user
        assert "json note" in user
    # extractive stays concise — no two-step note protocol
    ext = build_answer_messages("q?", "mem", AnswerMode.EXTRACTIVE)[-1]["content"].lower()
    assert "step 1" not in ext


def test_preference_prompt_applies_not_refuses() -> None:
    user = build_answer_messages("q?", "mem", AnswerMode.PREFERENCE)[-1]["content"].lower()
    assert "applying" in user or "apply" in user
    assert "do not refuse" in user


def test_extractive_prompt_forces_answer_when_span_exists() -> None:
    """T3.9 (class G over-abstention): a supporting span in the memories must
    force an answer — never IDK with the evidence present — and the answer is
    the bare fact/value (Tier-0 finalization: no sentence wrapper)."""
    user = build_answer_messages("q?", "mem", AnswerMode.EXTRACTIVE)[-1]["content"].lower()
    assert "span" in user
    assert "must answer" in user
    assert "just the fact" in user or "value only" in user or "just the value" in user
    assert "i don't know" in user  # the escape survives for the no-span case


def test_preference_prompt_names_the_applied_preference() -> None:
    """T3.8 (application signal): the answer must reference the stated
    preference it applies, so the application is visible, not implicit."""
    user = build_answer_messages("q?", "mem", AnswerMode.PREFERENCE)[-1]["content"].lower()
    assert "name the stated preference" in user or "reference the stated preference" in user


def test_build_messages_includes_memory_block_and_question() -> None:
    msgs = build_answer_messages("How many cats?", "[recorded 2023/01/01]\nA cat", AnswerMode.AGGREGATION)
    assert msgs[0]["role"] == "system"
    assert "How many cats?" in msgs[-1]["content"]
    assert "[recorded 2023/01/01]" in msgs[-1]["content"]


def test_suggested_max_tokens_scales_for_reasoning() -> None:
    assert suggested_max_tokens(AnswerMode.EXTRACTIVE, 256) == 256
    # all analytical modes need room for CoN notes + derivation + answer
    assert suggested_max_tokens(AnswerMode.TEMPORAL, 256) == 768
    assert suggested_max_tokens(AnswerMode.AGGREGATION, 256) == 768
    assert suggested_max_tokens(AnswerMode.PREFERENCE, 256) == 768


def test_chronological_sort_orders_by_recorded_date() -> None:
    from core.agents.answer_synthesis import chronological_sort

    docs = [
        "[recorded 2023/05/15] later fact",
        "[recorded 2021/01/01] earliest fact",
        "[recorded 2022/06/30] middle fact",
    ]
    out = chronological_sort(docs)
    assert out[0].endswith("earliest fact")
    assert out[1].endswith("middle fact")
    assert out[2].endswith("later fact")


def test_chronological_sort_undated_go_last_stably() -> None:
    from core.agents.answer_synthesis import chronological_sort

    docs = ["no date A", "[recorded 2023/01/01] dated", "no date B"]
    out = chronological_sort(docs)
    assert out[0].endswith("dated")
    assert out[1] == "no date A" and out[2] == "no date B"  # stable order preserved


def test_extract_final_answer_pulls_marker() -> None:
    assert extract_final_answer("MoMA Jan 8, Met Jan 15.\nAnswer: 7 days") == "7 days"
    # last marker wins
    assert extract_final_answer("Answer: draft\nmore\nAnswer: final") == "final"
    # no marker → unchanged (extractive mode)
    assert extract_final_answer("Business Administration") == "Business Administration"


# ---------------------------------------------------------------------------
# COMPILED_SUMMARY — the router's intent decides overview questions
# ---------------------------------------------------------------------------

class TestCompiledSummaryMode:
    """The surface router and this classifier used to disagree on one question.

    The router sent "Tell me about X" to the compiled-summary surface and put a
    wiki page at the head of the context; this function, seeing the same text,
    returned EXTRACTIVE, whose rules demand "just the fact/value ... no sentence
    wrapper". The reader was told to summarise and to not write sentences.
    """

    def test_overview_question_with_router_intent_selects_the_new_mode(self):
        assert classify_answer_mode(
            "Tell me about Kubernetes", intent="compiled_summary",
        ) is AnswerMode.COMPILED_SUMMARY
        assert classify_answer_mode(
            "What is Kubernetes?", intent="compiled_summary",
        ) is AnswerMode.COMPILED_SUMMARY

    def test_without_intent_the_classification_is_unchanged(self):
        """Every other caller omits intent and must be byte-identical."""
        assert classify_answer_mode("What is Kubernetes?") is AnswerMode.EXTRACTIVE
        assert classify_answer_mode("Tell me about Kubernetes") is AnswerMode.EXTRACTIVE

    def test_analytical_questions_outrank_intent(self):
        """The ordering that protects the LongMemEval temporal/count capability.

        The router can route an analytical question to the wiki surface; intent
        must not be able to demote it out of its date-arithmetic path.
        """
        assert classify_answer_mode(
            "When did I first mention Tesla?", intent="compiled_summary",
        ) is AnswerMode.TEMPORAL
        assert classify_answer_mode(
            "How many times did I mention Tesla?", intent="compiled_summary",
        ) is AnswerMode.AGGREGATION

    def test_oracle_question_type_still_wins(self):
        assert classify_answer_mode(
            "anything", "temporal-reasoning", intent="compiled_summary",
        ) is AnswerMode.TEMPORAL

    def test_prompt_forbids_outside_knowledge_and_preamble(self):
        msgs = build_answer_messages(
            "What is Kubernetes?", "[memory 1] Kubernetes is an orchestrator.",
            AnswerMode.COMPILED_SUMMARY,
        )
        rules = msgs[-1]["content"].lower()
        assert "do not add background knowledge" in rules
        assert "no preamble" in rules
        assert "unsupported claim" in rules

    def test_token_budget_sits_between_extractive_and_analytical(self):
        assert suggested_max_tokens(AnswerMode.COMPILED_SUMMARY) == 512
        assert suggested_max_tokens(AnswerMode.EXTRACTIVE) == 256
        assert suggested_max_tokens(AnswerMode.TEMPORAL) == 768
