# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-0 verifiability harness — the STREAMING-STATE CONSISTENCY probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 2.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``.

``verify_response_streaming`` maintains FOUR independently-evolving projections
of per-claim verification state — the SSE ``claim_verified`` wire event, the
Redis ``hall:{cid}`` snapshot, the Neo4j report, and the memory-promotion input.
Because there is no single settlement authority, the projections diverge: the
wire event drops fields the persisted report keeps (CR-042), the Redis snapshot
is written pre-sweep and never rewritten (CR-044), and more.

This probe drives the REAL async generator with a deterministic single claim
(patched heuristic extraction + a stubbed ``verify_claim`` carrying NLI +
memory_source) and captures all projections, then asserts they agree.

RED-then-GREEN: the divergence probe is ``xfail(strict=True)`` keyed to CR-042;
it flips to a live gate when the Phase-2 settlement-ledger + total wire builder
land. Do NOT tag ``preservation`` while xfailed.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import core.agents.hallucination.streaming as streaming

# A response comfortably above HALLUCINATION_MIN_RESPONSE_LENGTH that our patched
# heuristic extractor reduces to exactly one claim.
_RESPONSE = (
    "The sky is blue during the daytime because molecules in the atmosphere "
    "scatter blue light more strongly than red light across the sky overhead."
)


class _CaptureRedis:
    """Fake redis capturing hall:{cid} setex payloads; no-ops the metric
    side-channels (rpush/zadd) the generator best-effort-writes."""

    def __init__(self):
        self.setex_calls: list[tuple[str, str]] = []

    def setex(self, key, ttl, payload):
        self.setex_calls.append((key, payload))

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


def _stub_verified_claim(**overrides):
    result = {
        "status": "verified",
        "confidence": 0.9,
        "similarity": 0.9,
        "nli_entailment": 0.9,
        "nli_contradiction": 0.05,
        "memory_source": True,
        "source_filename": "doc.md",
        "reason": "supported by KB",
        "claim_type": "factual",
        "verification_method": "kb",
        "verification_model": "test-model",
    }
    result.update(overrides)
    return result


async def _drive_single_claim(monkeypatch):
    """Drive the real generator on a single deterministic verified claim.
    Returns (events, captured_redis, captured_report)."""
    monkeypatch.setattr(streaming, "_extract_claims_heuristic",
                        lambda text: ["The sky is blue during the daytime."])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic",
                        lambda claims, *a, **k: claims)

    async def _fake_verify_claim(*a, **k):
        return _stub_verified_claim()

    monkeypatch.setattr(streaming, "verify_claim", _fake_verify_claim)

    captured_report: dict = {}

    def _save_report_fn(**kwargs):
        captured_report.update(kwargs)

    redis = _CaptureRedis()
    events = []
    async for event in streaming.verify_response_streaming(
        response_text=_RESPONSE,
        conversation_id="e1-stream-conv",
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=redis,
        save_report_fn=_save_report_fn,
    ):
        events.append(event)
    return events, redis, captured_report


def _persisted_claim0(redis: _CaptureRedis) -> dict:
    assert redis.setex_calls, "generator never persisted a hall:{cid} snapshot"
    payload = json.loads(redis.setex_calls[-1][1])
    claims = payload.get("claims", [])
    assert claims, "persisted report carried no claims"
    return claims[0]


# ---------------------------------------------------------------------------
# CR-042 — the SSE wire event must carry the NLI + memory_source fields the
# persisted report keeps (one settled truth, not four projections).
# ---------------------------------------------------------------------------

# E1 Phase 2a CLOSED CR-042: _claim_verified_event now emits nli_entailment/
# nli_contradiction/memory_source so the SSE wire shape matches the persisted
# report. Live preservation gate — a regression that drops them fails the merge.
@pytest.mark.preservation
async def test_wire_event_carries_nli_and_memory_source(monkeypatch):
    events, redis, _report = await _drive_single_claim(monkeypatch)

    wire = [e for e in events if e.get("type") == "claim_verified"]
    assert wire, "generator yielded no claim_verified event"
    wire_event = wire[0]
    persisted = _persisted_claim0(redis)

    # The persisted report computes and keeps these (sanity — the green anchor
    # asserts this too); the wire event must not drop them.
    assert "nli_entailment" in persisted  # precondition: computed + persisted
    for field in ("nli_entailment", "nli_contradiction", "memory_source"):
        assert field in wire_event, (
            f"claim_verified wire event dropped '{field}' that the persisted "
            f"report retains — streamed verdict diverges from stored truth (CR-042)"
        )


# ---------------------------------------------------------------------------
# GREEN anchors — hold now and after Phase 2 (they validate the harness + pin
# the invariant that the fields ARE computed and persisted; the bug is purely
# the wire projection dropping them).
# ---------------------------------------------------------------------------

async def test_green_anchor_persisted_report_retains_nli(monkeypatch):
    """The persisted Redis report keeps the NLI + memory_source fields — proves
    they are computed and stored, so CR-042 is a wire-projection drop, not a
    compute gap. Validates the probe harness drives the real generator."""
    _events, redis, _report = await _drive_single_claim(monkeypatch)
    persisted = _persisted_claim0(redis)
    for field in ("nli_entailment", "nli_contradiction", "memory_source"):
        assert field in persisted, (
            f"persisted report unexpectedly lacks '{field}' — the probe harness "
            f"is not driving the verify pipeline as intended"
        )


async def test_green_anchor_single_claim_yields_one_verified_event(monkeypatch):
    """A single verified claim yields exactly one claim_verified event and a
    terminal persisted event — pins the happy-path shape the Phase-2 settlement
    ledger must preserve."""
    events, _redis, _report = await _drive_single_claim(monkeypatch)
    types = [e.get("type") for e in events]
    assert types.count("claim_verified") == 1, f"expected 1 claim_verified, got {types}"
    assert "persisted" in types, f"expected a terminal persisted event, got {types}"
