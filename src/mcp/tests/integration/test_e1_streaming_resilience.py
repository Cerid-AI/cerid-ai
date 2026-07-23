# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-3c verifiability harness — STREAMING RESILIENCE probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-093).

The streaming local dispatch path lacked the resilience envelope the non-streaming
path has. Its worst symptom: ``call_internal_llm_stream`` swallowed a mid-stream
drop (a failure *after* the first token) with a silent ``return`` and no
``inference_health`` record — so a **truncated answer was indistinguishable from a
complete one**, and the inline-gate consumer (``gated_synthesis``) computed
claims/citations over the partial text and presented it as verified.

3c makes ``call_internal_llm_stream`` RECORD the degradation and RAISE on a
mid-stream failure (instead of silently truncating), and ``gated_synthesis`` catch
that and fall back to a complete non-streaming synthesis. RED-then-GREEN; GREEN ->
preservation gates.
"""
from __future__ import annotations

import httpx
import pytest

import core.utils.internal_llm as internal_llm


@pytest.mark.preservation
async def test_midstream_failure_raises_and_records_instead_of_silent_truncation(
    monkeypatch,
):
    """A local stream that drops AFTER the first token must raise (so the caller
    knows it is incomplete) and record an inference_health fallback — not silently
    return a truncated answer. RED on HEAD (CR-093): the path logs and returns."""
    monkeypatch.setattr(
        internal_llm, "_resolve_stage_provider", lambda stage, default: "quenchforge"
    )

    async def _failing_stream(*a, **k):
        yield "The sky is "
        raise httpx.ConnectError("mid-stream drop")

    monkeypatch.setattr(internal_llm, "_stream_ollama", _failing_stream)

    fallback_calls: list[dict] = []

    def _spy_fallback(workload, **kwargs):
        fallback_calls.append({"workload": workload, **kwargs})

    monkeypatch.setattr(
        "core.utils.inference_health.record_fallback", _spy_fallback
    )

    collected: list[str] = []
    with pytest.raises(httpx.ConnectError):
        async for chunk in internal_llm.call_internal_llm_stream(
            [{"role": "user", "content": "why is the sky blue?"}], stage="probe"
        ):
            collected.append(chunk)

    assert collected == ["The sky is "], (
        "the partial content yielded before the drop should have reached the "
        f"consumer, got {collected!r}"
    )
    assert fallback_calls, (
        "mid-stream local failure was not recorded to inference_health — the "
        "streaming path is invisible to /health + the breaker (CR-093)"
    )
    assert fallback_calls[-1]["workload"] == "llm"


@pytest.mark.preservation
async def test_gated_synthesis_falls_back_to_complete_answer_on_stream_failure(
    monkeypatch,
):
    """When the inline-gated stream fails mid-way, gated_synthesis must return a
    COMPLETE non-streaming answer, not the silently-truncated partial (over which
    claims/citations would otherwise be computed). RED on HEAD (CR-093): the
    truncation propagates / the partial is returned."""
    import core.agents.hallucination.inline_gate as inline_gate

    async def _passthrough_gate(stream, *a, **k):
        async for piece in stream:
            yield piece

    monkeypatch.setattr(inline_gate, "inline_nli_gate", _passthrough_gate)

    async def _failing_stream(*a, **k):
        yield "The sky is "
        raise httpx.ConnectError("mid-stream drop")

    async def _complete_nonstreaming(*a, **k):
        return "The sky is blue because of Rayleigh scattering."

    monkeypatch.setattr(
        "core.utils.internal_llm.call_internal_llm_stream", _failing_stream
    )
    monkeypatch.setattr(
        "core.utils.internal_llm.call_internal_llm", _complete_nonstreaming
    )

    result = await inline_gate.gated_synthesis(
        [{"role": "user", "content": "why is the sky blue?"}],
        context=[],
        stage="probe",
    )

    assert result == "The sky is blue because of Rayleigh scattering.", (
        "gated_synthesis returned a truncated/incomplete answer on a mid-stream "
        f"failure instead of the complete non-streaming fallback (got {result!r}) "
        "(CR-093)"
    )
