# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-2b verifiability harness — STREAMING PERSIST-AUTHORITY probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 2.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-044, CR-045).

``verify_response_streaming`` builds the report and writes the Redis ``hall:{cid}``
snapshot BEFORE the Round-2 retry sweep re-verifies timed-out claims and mutates
``collected_results`` in place. The corrected verdicts reach the live SSE stream
and the (post-sweep) Neo4j write, but:

- **CR-044** — there is no second ``hall:{cid}`` write, so ``GET
  /agent/hallucination/{cid}`` and feedback indexing keep serving the stale
  pre-sweep report.
- **CR-045** — ``promote_verified_facts`` is gated on the POST-sweep verified
  count but handed the PRE-sweep report, so a claim the sweep resolved to
  ``verified`` is never promoted (its status in the passed report is still the
  timeout verdict).

This probe drives the REAL generator with a claim that times out on the first
verification and resolves to ``verified`` on the sweep retry, and asserts the
persisted Redis report + the report handed to promotion both carry the resolved
verdict. RED-then-GREEN; GREEN -> preservation gates.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

import core.agents.hallucination.streaming as streaming

_RESPONSE = (
    "The sky is blue during the daytime because atmospheric molecules scatter "
    "short-wavelength blue light more strongly than the longer red wavelengths."
)
_CLAIM = "The sky is blue during the daytime."


class _CaptureRedis:
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


async def _fake_extract(response_text, user_query=None):
    return [_CLAIM], "heuristic"


async def _drive_sweep(monkeypatch):
    """Drive the real generator on one claim that TIMES OUT on the main pass and
    resolves to 'verified' on the retry sweep. Returns (redis, captured_promote)."""
    monkeypatch.setattr(streaming, "extract_claims", _fake_extract)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic", lambda t: [_CLAIM])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic", lambda c, *a, **k: c)
    monkeypatch.setattr(streaming.config, "ENABLE_VERIFIED_MEMORY_PROMOTION", True,
                        raising=False)

    calls: dict[str, int] = {}

    async def _timeout_then_verified(claim_text, *a, **k):
        calls[claim_text] = calls.get(claim_text, 0) + 1
        if calls[claim_text] == 1:
            # Main pass: a timeout verdict — lands in the sweep's retry_indices.
            return {"status": "uncertain", "verification_method": "timeout",
                    "similarity": 0.0, "confidence": 0.0, "reason": "timed out"}
        # Sweep retry: resolves to verified.
        return {"status": "verified", "verification_method": "kb", "similarity": 0.9,
                "confidence": 0.9, "nli_entailment": 0.9, "nli_contradiction": 0.05,
                "memory_source": False, "reason": "supported by KB",
                "source_filename": "doc.md"}

    monkeypatch.setattr(streaming, "verify_claim", _timeout_then_verified)

    captured: dict = {}

    async def _spy_promote(report, **k):
        captured["report"] = report

    monkeypatch.setattr("core.agents.verified_memory.promote_verified_facts", _spy_promote)

    redis = _CaptureRedis()
    async for _ev in streaming.verify_response_streaming(
        response_text=_RESPONSE,
        conversation_id="e1-sweep",
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=redis,
        create_memory_fn=lambda *a, **k: object(),  # non-None → promotion fires
    ):
        pass
    # Let the fire-and-forget promotion task run.
    for _ in range(5):
        await asyncio.sleep(0)
    return redis, captured


@pytest.mark.preservation
async def test_redis_report_reflects_post_sweep_verdict(monkeypatch):
    """The persisted hall:{cid} report must carry the sweep-resolved 'verified'
    verdict, not the pre-sweep timeout. RED on HEAD: Redis is written pre-sweep and
    never rewritten (CR-044)."""
    redis, _ = await _drive_sweep(monkeypatch)

    assert redis.setex_calls, "generator never persisted a hall:{cid} report"
    payload = json.loads(redis.setex_calls[-1][1])
    claims = payload.get("claims", [])
    assert claims, "persisted report carried no claims"
    assert claims[0]["status"] == "verified", (
        "persisted Redis report kept the pre-sweep timeout verdict instead of the "
        "sweep-resolved 'verified' — GET /agent/hallucination/{cid} serves stale "
        "data (CR-044)"
    )
    assert payload["summary"]["verified"] == 1, "persisted summary count is pre-sweep"


@pytest.mark.preservation
async def test_promotion_receives_post_sweep_report(monkeypatch):
    """Memory promotion must receive the settled (post-sweep) report so a
    sweep-resolved verified claim is actually promoted. RED on HEAD: it gets the
    pre-sweep report (CR-045)."""
    _, captured = await _drive_sweep(monkeypatch)

    report = captured.get("report")
    assert report is not None, "promotion was not invoked (verified_count gate)"
    assert report["claims"][0]["status"] == "verified", (
        "memory promotion received the stale pre-sweep report — a sweep-resolved "
        "verified claim is gated in by the post-sweep count but never promoted "
        "because its status in the passed report is still 'timeout' (CR-045)"
    )
