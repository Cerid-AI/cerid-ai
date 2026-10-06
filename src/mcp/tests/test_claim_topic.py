# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A claim topic is a named subject, not the user's question."""

from core.agents.hallucination.extraction import _extract_topic_from_heading


def test_a_known_prefix_returns_the_remainder():
    assert _extract_topic_from_heading("", "Tell me about the Eiffel Tower") == "the Eiffel Tower"
    assert _extract_topic_from_heading("", "What is Python?") == "Python"
    assert _extract_topic_from_heading("", "What are tensors?") == "tensors"


def test_an_empty_remainder_is_not_a_topic():
    assert _extract_topic_from_heading("# Heading", "What is ?") is None


def test_an_unprefixed_question_uses_a_heading_and_not_the_question():
    text = "intro\n## Billing rules\nThe invoice is due Friday this week."
    assert _extract_topic_from_heading(text, "how much does it cost") == "Billing rules"


def test_an_unprefixed_question_without_a_heading_is_not_a_topic():
    text = "The invoice is due on Friday this week."
    assert _extract_topic_from_heading(text, "how much does it cost") is None


def test_without_a_query_the_heading_wins_over_a_body_line():
    text = "short\n# A long heading about bridges\nThis sentence is definitely long enough."
    assert _extract_topic_from_heading(text, None) == "A long heading about bridges"


def test_without_a_query_or_heading_the_first_significant_line_is_kept():
    text = "short\nThis sentence is definitely long enough to name a topic."
    assert (
        _extract_topic_from_heading(text, None)
        == "This sentence is definitely long enough to name a topic."
    )
