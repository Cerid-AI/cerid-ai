# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The benchmark answers the way the web client does: grounded in what it injects."""

from __future__ import annotations

from grounding import RAG_SYSTEM_PREAMBLE, grounded_turn


def _src(i: int, relevance: float, content: str) -> dict:
    return {"artifact_id": f"a{i}", "domain": "finance", "relevance": relevance, "content": content}


def test_the_injected_documents_reach_the_model_and_are_what_the_judge_reads():
    system, contexts = grounded_turn([_src(1, 0.9, "The 2025 limit is $23,500.")])
    assert system is not None
    assert system.startswith(RAG_SYSTEM_PREAMBLE + "\n\n<document ")
    assert "The 2025 limit is $23,500." in system
    assert contexts == ["The 2025 limit is $23,500."]


def test_relevance_floor_is_relative_to_the_best_hit_and_at_most_three_are_injected():
    sources = [_src(i, rel, f"distinct chunk number {i} alpha{i} beta{i}") for i, rel in
               enumerate([0.8, 0.5, 0.3, 0.2, 0.1])]
    _, contexts = grounded_turn(sources)
    # floor = 0.15 * 0.8 = 0.12: the 0.1 hit is out, and the cap keeps the first three
    assert contexts == [s["content"] for s in sources[:3]]


def test_near_duplicate_chunks_are_injected_once():
    text = "a 401k plan lets employees save before tax with an employer match"
    _, contexts = grounded_turn([_src(1, 0.9, text), _src(2, 0.8, text + " too")])
    assert contexts == [text]


def test_nothing_retrieved_sends_no_system_message():
    assert grounded_turn([]) == (None, [])
