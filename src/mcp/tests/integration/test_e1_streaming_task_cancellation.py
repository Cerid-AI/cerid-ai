# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-2d verifiability harness — BACKGROUND-TASK CANCELLATION probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 2.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-105, CR-106).

``verify_response_streaming`` spawns two kinds of background work that outlive
the point their verdicts can be used:

- **CR-106** — when the total streaming deadline fires, both fallback paths
  ``break`` out of the ``as_completed`` loop WITHOUT cancelling the remaining
  per-claim verify tasks. Those tasks keep running ``verify_claim`` under the
  *process-global* claim-verify semaphore (``patterns._claim_verify_semaphore``,
  shared across every conversation), so already-settled claims keep consuming
  external LLM budget and semaphore slots that gate the NEXT message's
  verification — degraded runs cascade under load — and their eventual results
  are discarded (only the exited main loop stored them).
- **CR-105** — the background ``batch_task`` is referenced only by a 3-second
  shielded wait; nothing else awaits or cancels it. Its callback writes verdicts
  into the shared ``collected_results`` at an arbitrary time relative to the
  report snapshot / retry sweep / Neo4j persist, so which stores see the batch
  verdicts is a timing coin-flip (two identical runs persist different reports),
  and on generator teardown it is orphaned entirely.

This probe drives the REAL generator with a tiny total deadline and slow
``verify_claim`` / batch calls that record whether they were cancelled, then
asserts the in-flight verify tasks AND the background batch task are cancelled
on the deadline break (not left running). RED-then-GREEN; GREEN -> preservation
gates.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import core.agents.hallucination.streaming as streaming

_RESPONSE = (
    "The sky is blue during the daytime because atmospheric molecules scatter "
    "short-wavelength blue light. The ocean looks blue for a related but "
    "distinct reason involving water's absorption of longer wavelengths."
)
_CLAIMS = [
    "The sky is blue during the daytime.",
    "The ocean looks blue because water absorbs longer wavelengths.",
]


class _NoopRedis:
    def setex(self, *a, **k):
        return True

    def get(self, key):
        return None

    def setnx(self, *a, **k):
        return True

    def expire(self, *a, **k):
        return True

    def rpush(self, *a, **k):
        return 1

    def zadd(self, *a, **k):
        return 1

    def ltrim(self, *a, **k):
        return True


async def _fake_extract(response_text, user_query=None):
    return list(_CLAIMS), "heuristic"


def _patch_extraction(monkeypatch):
    monkeypatch.setattr(streaming, "extract_claims", _fake_extract)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic", lambda t: list(_CLAIMS))
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic", lambda c, *a, **k: c)
    # A tiny total deadline so the fallback break fires almost immediately, while
    # the (slow) verify / batch calls are still in flight.
    monkeypatch.setattr(streaming.config, "STREAMING_TOTAL_TIMEOUT", 0.2, raising=False)


async def _drain(monkeypatch):
    events = []
    async for ev in streaming.verify_response_streaming(
        response_text=_RESPONSE,
        conversation_id="e1-cancel",
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=_NoopRedis(),
    ):
        events.append(ev)
    return events


@pytest.mark.preservation
async def test_deadline_break_cancels_inflight_verify_tasks(monkeypatch):
    """On the total-deadline break, the still-running per-claim verify tasks
    must be cancelled — not left holding the process-global semaphore + burning
    LLM spend after sentinel verdicts shipped. RED on HEAD (CR-106): the loop
    breaks without cancelling them."""
    _patch_extraction(monkeypatch)

    state = {"entered": 0, "cancelled": 0, "completed": 0}

    async def _slow_verify(*a, **k):
        state["entered"] += 1
        try:
            await asyncio.sleep(2.0)  # never settles before the 0.2s total deadline
        except asyncio.CancelledError:
            state["cancelled"] += 1
            raise
        state["completed"] += 1
        return {"status": "verified", "similarity": 0.9}

    monkeypatch.setattr(streaming, "verify_claim", _slow_verify)

    await _drain(monkeypatch)

    assert state["entered"] >= 1, (
        "no verify_claim task entered before the deadline — the probe is not "
        "exercising the in-flight path"
    )
    assert state["cancelled"] >= 1, (
        "in-flight verify task was NOT cancelled on the deadline break — it keeps "
        "holding the process-global claim-verify semaphore + burning LLM spend "
        "and its verdict is discarded (CR-106)"
    )
    assert state["completed"] == 0, (
        "a verify task ran to completion after the deadline break — leaked work "
        "the exited main loop can never collect (CR-106)"
    )


@pytest.mark.preservation
async def test_deadline_break_cancels_background_batch_task(monkeypatch):
    """The background batch task must be cancelled on the deadline break, so it
    can't mutate collected_results at an arbitrary point relative to the report
    snapshot / sweep / persist (nondeterministic reports) or outlive teardown.
    RED on HEAD (CR-105): nothing cancels batch_task."""
    _patch_extraction(monkeypatch)

    # Force both claims onto the batch path: current-event + no cached verdict so
    # they populate current_event_claims (>=2 -> batch_task is created).
    monkeypatch.setattr(streaming, "_is_current_event_claim", lambda c: True)

    async def _no_cache(*a, **k):
        return None

    monkeypatch.setattr("core.utils.claim_cache.get_cached_verdict", _no_cache)

    batch_state = {"entered": 0, "cancelled": 0, "completed": 0}

    async def _slow_batch(claims_arg, **k):
        batch_state["entered"] += 1
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            batch_state["cancelled"] += 1
            raise
        batch_state["completed"] += 1
        return {}

    monkeypatch.setattr(
        "core.agents.hallucination.verification.verify_claims_batch_external",
        _slow_batch,
    )

    async def _slow_verify(*a, **k):
        await asyncio.sleep(2.0)
        return {"status": "verified", "similarity": 0.9}

    monkeypatch.setattr(streaming, "verify_claim", _slow_verify)

    await _drain(monkeypatch)

    assert batch_state["entered"] == 1, (
        "batch task never started — the probe is not exercising the batch path"
    )
    assert batch_state["cancelled"] == 1, (
        "background batch task was NOT cancelled on the deadline break — it can "
        "still write collected_results at a nondeterministic point vs. the report "
        "snapshot and is orphaned on teardown (CR-105)"
    )
    assert batch_state["completed"] == 0, (
        "batch task ran to completion after the deadline break — its late verdicts "
        "race the persisted report (CR-105)"
    )
