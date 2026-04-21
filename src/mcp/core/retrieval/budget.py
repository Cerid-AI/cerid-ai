# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RetrievalBudget — bounded-concurrency fan-out with wall-clock enforcement.

Every retrieval path that fans out across multiple sources (KB collections,
external data-source registry, memory recall, graph expansion) should run
inside a ``RetrievalBudget`` so a single slow source can't block the event
loop past the watchdog's 45s heartbeat threshold.

Design goals:
- Partial results on exhaustion — never an exception that aborts the caller.
- Per-source timeout capped by the remaining wall-clock budget, so a slow
  source at the tail end of the budget doesn't silently consume its full
  per-source hint when only 500ms are left.
- Structured summary for observability — the caller can surface which
  sources contributed to the returned result and which timed out.
- Zero dependencies beyond asyncio + stdlib.

Usage::

    async with RetrievalBudget(wall_clock=8.0, per_source_hint=2.0) as budget:
        results = await budget.gather({
            "kb": kb_task(query),
            "memory": memory_recall(query),
            "external": registry.query_all(query),
        })
    logger.info("retrieval summary: %s", budget.summary())
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from typing import Any

logger = logging.getLogger("ai-companion.retrieval_budget")


class RetrievalBudget:
    """Context manager enforcing a wall-clock budget across retrieval sources.

    Parameters
    ----------
    wall_clock : float
        Maximum total seconds the entire fan-out may consume. Once exhausted,
        not-yet-completed sources are cancelled and their contribution is
        omitted from the result dict.
    per_source_hint : float
        Preferred per-source timeout. Actual enforced timeout is
        ``min(per_source_hint, remaining_budget)`` so sources near the end of
        the window don't silently eat the whole remainder.
    label : str
        Optional label for log correlation across nested budgets.
    """

    def __init__(
        self,
        wall_clock: float,
        per_source_hint: float = 2.0,
        *,
        label: str = "",
    ) -> None:
        if wall_clock <= 0:
            raise ValueError("wall_clock must be positive")
        if per_source_hint <= 0:
            raise ValueError("per_source_hint must be positive")
        self._wall_clock = wall_clock
        self._per_source = per_source_hint
        self._label = label
        self._start: float = 0.0
        self._entries: list[dict[str, Any]] = []

    @property
    def remaining(self) -> float:
        """Seconds left before the budget is exhausted. Zero = no more time."""
        return max(0.0, self._wall_clock - (time.monotonic() - self._start))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    async def __aenter__(self) -> "RetrievalBudget":
        self._start = time.monotonic()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # Log overruns so they're visible even when the caller forgets to
        # print summary(). An overrun isn't a bug per se — it just means the
        # happy-path timings don't match the budget and someone should tune.
        if self.elapsed > self._wall_clock * 1.1:
            logger.warning(
                "RetrievalBudget%s overran: %.1fms used of %.1fms budget",
                f" [{self._label}]" if self._label else "",
                self.elapsed * 1000,
                self._wall_clock * 1000,
            )

    async def _run_one(
        self, name: str, coro: Awaitable[Any],
    ) -> tuple[str, Any]:
        """Run a single source with the remaining-budget-capped timeout."""
        t0 = time.monotonic()
        effective_timeout = min(self._per_source, self.remaining)
        if effective_timeout <= 0:
            self._entries.append({
                "name": name, "ms": 0.0, "ok": False, "reason": "budget_exhausted",
            })
            # Cancel the coro — it never started awaiting, this prevents leaks.
            if asyncio.iscoroutine(coro):
                coro.close()
            return name, None
        try:
            result = await asyncio.wait_for(coro, timeout=effective_timeout)
            self._entries.append({
                "name": name,
                "ms": round((time.monotonic() - t0) * 1000, 1),
                "ok": True,
                "reason": "ok",
            })
            return name, result
        except asyncio.TimeoutError:
            ms = round((time.monotonic() - t0) * 1000, 1)
            self._entries.append({
                "name": name, "ms": ms, "ok": False, "reason": "timeout",
            })
            logger.warning(
                "RetrievalBudget%s: source '%s' timed out after %.1fms",
                f" [{self._label}]" if self._label else "", name, ms,
            )
            return name, None
        except asyncio.CancelledError:
            # Re-raise so the surrounding cancel-scope behaves correctly.
            self._entries.append({
                "name": name,
                "ms": round((time.monotonic() - t0) * 1000, 1),
                "ok": False,
                "reason": "cancelled",
            })
            raise
        except Exception as exc:
            ms = round((time.monotonic() - t0) * 1000, 1)
            self._entries.append({
                "name": name, "ms": ms, "ok": False,
                "reason": f"error:{type(exc).__name__}",
            })
            logger.warning(
                "RetrievalBudget%s: source '%s' errored after %.1fms: %s",
                f" [{self._label}]" if self._label else "", name, ms, exc,
            )
            return name, None

    async def gather(
        self, sources: dict[str, Awaitable[Any]],
    ) -> dict[str, Any]:
        """Run sources concurrently; return {name: result} for completed ones.

        Sources that time out, error, or miss the budget return None. The
        return dict always contains every input key — callers check for
        None rather than KeyError.
        """
        if not sources:
            return {}
        tasks = [self._run_one(name, coro) for name, coro in sources.items()]
        pairs = await asyncio.gather(*tasks, return_exceptions=False)
        return dict(pairs)

    def summary(self) -> dict[str, Any]:
        """Structured observability payload."""
        return {
            "label": self._label,
            "budget_ms": round(self._wall_clock * 1000, 1),
            "elapsed_ms": round(self.elapsed * 1000, 1),
            "sources": list(self._entries),
            "ok_count": sum(1 for e in self._entries if e["ok"]),
            "total_count": len(self._entries),
        }
