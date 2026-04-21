# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for core/retrieval/budget.py — the wall-clock-enforcing fan-out helper."""

from __future__ import annotations

import asyncio

import pytest

from core.retrieval.budget import RetrievalBudget


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _slow(name: str, delay: float, value: str = "ok") -> str:
    await asyncio.sleep(delay)
    return f"{name}:{value}"


async def _boom(name: str, delay: float = 0.01) -> str:
    await asyncio.sleep(delay)
    raise RuntimeError(f"{name}-error")


def test_happy_path_all_complete():
    async def run():
        async with RetrievalBudget(wall_clock=2.0, per_source_hint=1.0) as b:
            results = await b.gather({
                "a": _slow("a", 0.05),
                "b": _slow("b", 0.05),
                "c": _slow("c", 0.05),
            })
        return results, b.summary()

    results, summary = _run(run())
    assert results == {"a": "a:ok", "b": "b:ok", "c": "c:ok"}
    assert summary["ok_count"] == 3
    assert summary["total_count"] == 3
    assert summary["elapsed_ms"] < 200  # all ran in parallel


def test_one_source_timeout_does_not_block_others():
    async def run():
        async with RetrievalBudget(wall_clock=1.0, per_source_hint=0.2) as b:
            results = await b.gather({
                "fast": _slow("fast", 0.05),
                "slow": _slow("slow", 5.0),   # exceeds per-source
                "medium": _slow("medium", 0.1),
            })
        return results, b.summary()

    results, summary = _run(run())
    assert results["fast"] == "fast:ok"
    assert results["medium"] == "medium:ok"
    assert results["slow"] is None  # timed out
    slow_entry = next(e for e in summary["sources"] if e["name"] == "slow")
    assert slow_entry["reason"] == "timeout"
    assert slow_entry["ok"] is False


def test_budget_exhaustion_skips_late_sources():
    """Sources registered after budget is already spent should skip cleanly,
    not consume the rest of the wall-clock waiting."""
    async def run():
        async with RetrievalBudget(wall_clock=0.15, per_source_hint=0.5) as b:
            # The first two sources will eat the whole budget
            results = await b.gather({
                "slow1": _slow("slow1", 0.2),
                "slow2": _slow("slow2", 0.2),
            })
        return results, b.summary()

    results, summary = _run(run())
    assert results["slow1"] is None
    assert results["slow2"] is None
    # Both should have timed out; total elapsed ~= budget, not 2x slow
    assert summary["elapsed_ms"] < 300


def test_exception_in_source_yields_none_and_logs():
    async def run():
        async with RetrievalBudget(wall_clock=1.0) as b:
            results = await b.gather({
                "ok": _slow("ok", 0.01),
                "bad": _boom("bad"),
            })
        return results, b.summary()

    results, summary = _run(run())
    assert results["ok"] == "ok:ok"
    assert results["bad"] is None
    bad_entry = next(e for e in summary["sources"] if e["name"] == "bad")
    assert bad_entry["reason"].startswith("error:")
    assert bad_entry["ok"] is False


def test_empty_sources_returns_empty_dict():
    async def run():
        async with RetrievalBudget(wall_clock=1.0) as b:
            results = await b.gather({})
        return results

    results = _run(run())
    assert results == {}


def test_invalid_budget_raises():
    with pytest.raises(ValueError):
        RetrievalBudget(wall_clock=0)
    with pytest.raises(ValueError):
        RetrievalBudget(wall_clock=-1.0)
    with pytest.raises(ValueError):
        RetrievalBudget(wall_clock=1.0, per_source_hint=0)


def test_agent_query_budget_gate_returns_degraded_on_timeout():
    """The public agent_query entry wraps the impl with asyncio.wait_for;
    when the impl would exceed AGENT_QUERY_BUDGET_SECONDS, a structured
    degraded response is returned instead of a raised TimeoutError.

    This is the watchdog-safety regression — if this test fails, a slow
    retrieval can again exceed the 45s event-loop watchdog heartbeat."""
    from unittest.mock import patch

    from core.agents import query_agent

    async def _slow_impl(*args, **kwargs):
        await asyncio.sleep(10)  # definitely longer than the test budget
        return {"should": "never return"}

    async def run():
        # Tight budget so the test runs fast
        with (
            patch("config.AGENT_QUERY_BUDGET_SECONDS", 0.1),
            patch.object(query_agent, "_agent_query_impl", _slow_impl),
        ):
            return await query_agent.agent_query(query="anything", top_k=5)

    result = _run(run())
    assert result["budget_exceeded"] is True
    assert result["strategy"] == "degraded_budget_exhausted"
    assert result["source_status"]["kb"] == "timeout"
    assert result["source_status"]["external"] == "timeout"
    assert "longer than the configured budget" in result["degraded_reason"]


def test_per_source_timeout_capped_by_remaining_budget():
    """Even if per_source_hint is generous, the actual timeout shouldn't
    exceed the remaining wall-clock — otherwise a slow tail source silently
    blows the budget."""
    async def run():
        async with RetrievalBudget(wall_clock=0.3, per_source_hint=10.0) as b:
            results = await b.gather({
                "slow": _slow("slow", 5.0),
            })
        return results, b.summary()

    results, summary = _run(run())
    assert results["slow"] is None
    # Actual elapsed should be near the wall_clock, not per_source_hint
    assert summary["elapsed_ms"] < 500
