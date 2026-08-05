# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-2c verifiability harness — TIMEOUT-FALLBACK SETTLEMENT probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 2.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-036, CR-037).

When the total streaming deadline fires, the fallback path synthesised
``claim_verified`` events that:

- **CR-036** — nested the verdict under a ``verdict`` key, set ``source`` to the
  sentinel strings ``kb_only_timeout`` / ``timeout`` (which the FE renders as the
  claim's Source), and omitted the top-level ``status`` / ``verification_method``
  the shared ``_claim_verified_event`` builder sets — a shape divergent from every
  other claim event.
- **CR-037** — incremented the summary counters but never wrote the verdict into
  ``collected_results``, so the persisted ``hall:{cid}`` claims array (compacted
  from ``collected_results``) omitted the timed-out claims while the summary counts
  included them, and the server's feedback index space diverged from the UI's.

This probe drives the REAL generator with a tiny total deadline and a slow
``verify_claim`` so the total-deadline fallback fires, and asserts the fallback
event has the canonical shape AND the timed-out claim lands in the persisted
report. RED-then-GREEN; GREEN -> preservation gates.
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


async def _drive_timeout(monkeypatch):
    """Drive the real generator so the TOTAL deadline fires before the (slow)
    verify_claim settles the claim. Returns (events, redis)."""
    monkeypatch.setattr(streaming, "extract_claims", _fake_extract)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic", lambda t: [_CLAIM])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic", lambda c, *a, **k: c)
    monkeypatch.setattr(streaming.config, "STREAMING_TOTAL_TIMEOUT", 0.05, raising=False)

    async def _slow_verify(*a, **k):
        await asyncio.sleep(1.0)  # never settles before the 0.05s total deadline
        return {"status": "verified", "similarity": 0.9}

    monkeypatch.setattr(streaming, "verify_claim", _slow_verify)

    redis = _CaptureRedis()
    events = []
    async for ev in streaming.verify_response_streaming(
        response_text=_RESPONSE,
        conversation_id="e1-timeout",
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=redis,
    ):
        events.append(ev)
    return events, redis


@pytest.mark.preservation
async def test_timeout_fallback_event_has_canonical_shape(monkeypatch):
    """The total-deadline fallback claim_verified event must match the shared
    builder shape: top-level status + verification_method, no nested 'verdict',
    and no sentinel leaking into 'source'. RED on HEAD (CR-036)."""
    events, _redis = await _drive_timeout(monkeypatch)

    wire = [e for e in events if e.get("type") == "claim_verified"]
    assert wire, "generator emitted no claim_verified event for the timed-out claim"
    ev = wire[-1]

    assert "verdict" not in ev, (
        "timeout fallback event nested the verdict under 'verdict' — divergent from "
        "every other claim event (CR-036)"
    )
    assert ev.get("status") == "uncertain", (
        "timeout fallback event lacks the top-level status the shared builder sets "
        f"(got {ev.get('status')!r}) (CR-036)"
    )
    assert ev.get("verification_method") == "timeout", (
        "timeout fallback event omitted top-level verification_method (CR-036)"
    )
    assert ev.get("source") not in ("timeout", "kb_only_timeout"), (
        f"sentinel label leaked into 'source' (={ev.get('source')!r}), rendered as "
        f"the claim's Source in the UI (CR-036)"
    )


@pytest.mark.preservation
async def test_timeout_claim_is_written_to_the_persisted_report(monkeypatch):
    """The timed-out claim's verdict must land in collected_results so the
    persisted report's claims array agrees with its summary counts (and the
    feedback index space). RED on HEAD: counted but never collected (CR-037)."""
    _events, redis = await _drive_timeout(monkeypatch)

    assert redis.setex_calls, "generator never persisted a hall:{cid} report"
    payload = json.loads(redis.setex_calls[-1][1])
    claims = payload.get("claims", [])
    summary = payload.get("summary", {})

    assert summary.get("total") == 1
    assert summary.get("uncertain") == 1, "timed-out claim not counted uncertain"
    assert len(claims) == 1, (
        "the timed-out claim was counted in the summary but never written to "
        "collected_results, so the persisted claims array omits it — report and "
        "summary disagree and feedback indices diverge from the UI (CR-037)"
    )
    assert claims[0]["status"] == "uncertain"
