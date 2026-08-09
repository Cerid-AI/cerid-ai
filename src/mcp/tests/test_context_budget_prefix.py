# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-5 gate — model context-budget prefix resolution (CR-073).

``get_context_budget_for_model`` matched the FIRST prefix in insertion order, so
the general ``gpt-4o`` entry (40k) shadowed the more specific ``gpt-4o-mini``
entry (20k) and made it unreachable. The lookup now picks the LONGEST matching
prefix, so a specific family wins regardless of dict order.
"""
from __future__ import annotations

from config.settings import (
    MODEL_CONTEXT_CHAR_BUDGETS,
    QUERY_CONTEXT_MAX_CHARS,
    get_context_budget_for_model,
)


def test_gpt_4o_mini_is_not_shadowed_by_gpt_4o():
    # gpt-4o-mini has a distinct, smaller budget than gpt-4o; the general prefix
    # must not shadow it (CR-073).
    assert get_context_budget_for_model("openai/gpt-4o-mini") == MODEL_CONTEXT_CHAR_BUDGETS["gpt-4o-mini"]
    assert get_context_budget_for_model("openai/gpt-4o") == MODEL_CONTEXT_CHAR_BUDGETS["gpt-4o"]
    assert (
        MODEL_CONTEXT_CHAR_BUDGETS["gpt-4o-mini"] != MODEL_CONTEXT_CHAR_BUDGETS["gpt-4o"]
    ), "test is only meaningful while the two budgets differ"


def test_other_families_resolve_and_unknown_falls_back():
    assert get_context_budget_for_model("anthropic/claude-sonnet-4.5") == MODEL_CONTEXT_CHAR_BUDGETS["claude"]
    assert get_context_budget_for_model("x-ai/grok-4.5") == MODEL_CONTEXT_CHAR_BUDGETS["grok"]
    assert get_context_budget_for_model("some/unknown-model") == QUERY_CONTEXT_MAX_CHARS
    assert get_context_budget_for_model(None) == QUERY_CONTEXT_MAX_CHARS
