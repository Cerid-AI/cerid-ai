# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Verification-quality preservation invariants (2026-07-13 beta RCA).

These gates exist because the 2026-07-13 beta exposed failure modes the
suite never exercised: every claim timing out, a claim "verifying" against
the assistant's own prior transcript (kb_batch + conversations domain),
and stale-cutoff responses confirmed from KB snapshots.

Design note: CI corpora are near-empty, which historically hid envelope
invariants (see tasks/2026-07-12-beta-triage.md). The gates below are
chosen so they bite even on a degenerate corpus: the stale-cutoff routing
invariant is pure pipeline logic, and the all-timeout gate fails whenever
the pipeline's budget geometry regresses, corpus or not.
"""
from __future__ import annotations

import json
import time

import pytest

from .conftest import record_preservation_skip

pytestmark = pytest.mark.preservation

# The stream must finish comfortably inside the server's own total budget;
# a stream that needs more than budget + slack means the deadline layering
# regressed (per-claim wait_fors no longer fit the total window).
_STREAM_SLACK_S = 30.0


def _run_verify_stream(http_client, response_text: str, conversation_id: str,
                       user_query: str) -> list[dict]:
    """Drive /agent/verify-stream and return the parsed SSE events."""
    events: list[dict] = []
    with http_client.stream(
        "POST", "/agent/verify-stream",
        json={
            "response_text": response_text,
            "conversation_id": conversation_id,
            "user_query": user_query,
        },
    ) as resp:
        assert resp.status_code == 200, f"verify-stream HTTP {resp.status_code}"
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            try:
                events.append(json.loads(line[5:]))
            except json.JSONDecodeError:
                continue
    return events


def test_verify_stream_completes_and_not_all_timeouts(http_client, request):
    """The 2026-07-13 beta failure: all 7 claims died on per-claim timeouts
    and the report saved with methods=['cross_model', 'timeout'].

    Gate: the stream completes within budget+slack, emits a summary, and at
    least one claim resolves through something other than the timeout path.
    """
    from config import settings

    start = time.monotonic()
    events = _run_verify_stream(
        http_client,
        "Sally Ride became the first American woman in space in 1983. "
        "Water boils at 100 degrees Celsius at sea level.",
        "preservation-vq-timeout",
        "tell me about women in space",
    )
    elapsed = time.monotonic() - start

    assert elapsed < settings.STREAMING_TOTAL_TIMEOUT + _STREAM_SLACK_S, (
        f"verify-stream took {elapsed:.0f}s — deadline layering regressed"
    )
    summaries = [e for e in events if e.get("type") == "summary"]
    assert summaries, f"no summary event (events: {[e.get('type') for e in events]})"
    if summaries[0].get("skipped"):
        record_preservation_skip(
            request,
            "verification-quality",
            "verification skipped (feature off or response too short)",
        )

    claim_events = [e for e in events if e.get("type") == "claim_verified"]
    assert claim_events, "no claim_verified events for a two-claim response"
    methods = {e.get("verification_method") for e in claim_events}
    assert methods - {"timeout"}, (
        f"every claim resolved via the timeout path (methods={methods}) — "
        "the per-claim budget no longer fits the verification work"
    )


def test_stale_cutoff_response_never_confirmed_from_kb(http_client, request):
    """A response that admits a stale knowledge cutoff must not have its
    claims pre-resolved from KB snapshots (kb_batch), whatever the corpus
    contains — those claims need the live web path.

    This is the live twin of the unit regression in
    tests/test_verification_quality_regressions.py; it exercises the real
    /agent/verify-stream wiring end to end.
    """
    events = _run_verify_stream(
        http_client,
        "As of my knowledge cutoff in 2023, no woman has traveled around "
        "the Moon. The most recent lunar mission carried no crew.",
        "preservation-vq-stale",
        "how many women have been around the moon",
    )
    summaries = [e for e in events if e.get("type") == "summary"]
    assert summaries, "no summary event"
    if summaries[0].get("skipped"):
        record_preservation_skip(
            request,
            "verification-quality",
            "verification skipped (feature off or response too short)",
        )

    claim_events = [e for e in events if e.get("type") == "claim_verified"]
    kb_batch = [e for e in claim_events if e.get("verification_method") == "kb_batch"]
    assert not kb_batch, (
        "stale-cutoff response claims were confirmed from KB snapshots: "
        f"{[e.get('claim') for e in kb_batch]}"
    )


def test_kb_batch_verdicts_never_cite_conversations(http_client):
    """Whenever the kb_batch fast path fires on this instance, its evidence
    must come from a real KB domain — never a prior chat transcript (the
    live-proven circular-verification bug) and never with an empty
    artifact id.

    On an empty corpus this is vacuous (no kb_batch verdicts fire) — the
    unit regressions cover the logic; this gate bites on instances with
    real ingested conversations, where the bug actually manifested.
    """
    events = _run_verify_stream(
        http_client,
        "No woman has traveled around the Moon. The Artemis program has "
        "not launched a crewed mission.",
        "preservation-vq-circular",
        "how many women have been around the moon",
    )
    claim_events = [e for e in events if e.get("type") == "claim_verified"]
    for e in claim_events:
        if e.get("verification_method") == "kb_batch":
            assert e.get("source_domain") != "conversations", (
                f"claim verified against a chat transcript: {e.get('claim')}"
            )
            assert e.get("source_artifact_id"), (
                "kb_batch verdict without a source artifact"
            )
