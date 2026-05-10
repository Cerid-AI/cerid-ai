# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for core.processor.priority."""
from __future__ import annotations

import pytest

from core.processor.priority import PRIORITY_WEIGHT, Priority, priority_order


class TestPriorityOrder:
    def test_returns_high_medium_low(self) -> None:
        assert priority_order() == [Priority.HIGH, Priority.MEDIUM, Priority.LOW]

    def test_returns_all_three_priorities(self) -> None:
        order = priority_order()
        assert set(order) == {Priority.HIGH, Priority.MEDIUM, Priority.LOW}

    def test_length_is_three(self) -> None:
        assert len(priority_order()) == 3


class TestPriorityEnum:
    def test_values_are_lowercase_strings(self) -> None:
        assert Priority.LOW.value == "low"
        assert Priority.MEDIUM.value == "medium"
        assert Priority.HIGH.value == "high"

    def test_round_trip_from_value(self) -> None:
        for p in Priority:
            assert Priority(p.value) is p

    def test_comparable_as_string(self) -> None:
        # Priority inherits str, so string equality works
        assert Priority.HIGH == "high"
        assert Priority.MEDIUM == "medium"
        assert Priority.LOW == "low"

    def test_all_members_present(self) -> None:
        members = {p.value for p in Priority}
        assert members == {"low", "medium", "high"}

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            Priority("unknown")


class TestPriorityWeight:
    def test_high_weight_exceeds_medium(self) -> None:
        assert PRIORITY_WEIGHT[Priority.HIGH] > PRIORITY_WEIGHT[Priority.MEDIUM]

    def test_medium_weight_exceeds_low(self) -> None:
        assert PRIORITY_WEIGHT[Priority.MEDIUM] > PRIORITY_WEIGHT[Priority.LOW]

    def test_all_priorities_have_weight(self) -> None:
        for p in Priority:
            assert p in PRIORITY_WEIGHT
