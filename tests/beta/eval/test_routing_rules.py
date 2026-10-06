# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Routing tier rules — offline, no stack."""

from __future__ import annotations

import pytest
from routing_validation import classify_model_tier, routing_failure


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("capable", "free_or_cheap"),
        ("research_online", "capable"),
        ("research_online", "free_or_cheap"),
    ],
)
def test_downgrade_is_a_failure_naming_expected_and_actual(expected, actual):
    message = routing_failure(expected, actual, "some/model")
    assert message is not None
    assert f"expected '{expected}'" in message and f"got '{actual}'" in message and "some/model" in message


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("free_or_cheap", "free_or_cheap"),
        ("free_or_cheap", "capable"),
        ("capable", "capable"),
        ("capable", "research_online"),
        ("research_online", "research_online"),
    ],
)
def test_match_or_upgrade_is_not_a_failure(expected, actual):
    assert routing_failure(expected, actual, "some/model") is None


def test_unknown_tier_is_still_unknown():
    assert classify_model_tier("mystery/model-x") == "unknown"
    assert classify_model_tier("openai/gpt-4o-mini") == "free_or_cheap"
    assert classify_model_tier("anthropic/claude-sonnet-4.6") == "capable"
    assert classify_model_tier("x-ai/grok-4") == "research_online"
