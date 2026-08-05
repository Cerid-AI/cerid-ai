# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Contract test for the cost_class → p95 budget mapping.

The ``CostClass`` Literal in ``app.tool_registry`` documents three coarse
handler-cost tiers (low / medium / high) with p95 expectations. Operators
and SDK clients rely on those numbers being authoritative — drift between
the docstring and the runtime-readable mapping would silently change the
contract.

This module asserts:

1. Every registered tool's ``cost_class`` is one of the three documented
   tiers (no silent freelance values).
2. The runtime-readable ``COST_CLASS_P95_BUDGET_MS`` mapping covers all
   three tiers, in milliseconds, with values matching the docstring
   (200 / 2_000 / 8_000).
3. Tier budgets are monotonically increasing — a "high" call must have
   strictly more budget than "medium", which must have strictly more
   than "low". Otherwise the taxonomy is meaningless.

These are static contract assertions — no running stack needed. The
benchmark-slo job in CI is the runtime counterpart that actually measures
whether handlers stay within budget against a live MCP stack.
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.tool_registry import COST_CLASS_P95_BUDGET_MS, CostClass


def test_cost_class_values_match_documented_tiers() -> None:
    """The Literal type and the budget mapping use the same key set."""
    literal_values = set(get_args(CostClass))
    budget_keys = set(COST_CLASS_P95_BUDGET_MS.keys())
    assert literal_values == budget_keys, (
        f"CostClass tiers and COST_CLASS_P95_BUDGET_MS drifted: "
        f"only-in-Literal={literal_values - budget_keys}, "
        f"only-in-budget={budget_keys - literal_values}"
    )


@pytest.mark.parametrize(
    "tier,expected_ms",
    [("low", 200), ("medium", 2_000), ("high", 8_000)],
)
def test_tier_budget_matches_docstring(tier: str, expected_ms: int) -> None:
    """Budget values must equal the CostClass docstring numbers."""
    assert COST_CLASS_P95_BUDGET_MS[tier] == expected_ms


def test_tier_budgets_are_monotonically_increasing() -> None:
    """low < medium < high. Otherwise the tier system loses meaning."""
    low = COST_CLASS_P95_BUDGET_MS["low"]
    medium = COST_CLASS_P95_BUDGET_MS["medium"]
    high = COST_CLASS_P95_BUDGET_MS["high"]
    assert low < medium < high, (
        f"non-monotonic budgets: low={low} medium={medium} high={high}"
    )


def test_every_registered_tool_has_a_known_cost_class() -> None:
    """No tool may ship with a cost_class outside the Literal tiers."""
    # Force eager registration of all MCP tools so TOOL_REGISTRY is populated.
    import app.mcp_tools  # noqa: F401
    from app.tool_registry import TOOL_REGISTRY

    valid_tiers = set(get_args(CostClass))
    bad: list[tuple[str, str]] = []
    for name, tool in TOOL_REGISTRY.items():
        if tool.cost_class not in valid_tiers:
            bad.append((name, tool.cost_class))
    assert not bad, (
        "Tools registered with unknown cost_class values: " + repr(bad)
    )
