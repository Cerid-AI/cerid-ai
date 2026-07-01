# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 3 — streaming synthesis + inline NLI gating + verification coherence.

Covers the capability the architecture-claims gate asserts:
  * ``call_internal_llm_stream`` streams (and degrades gracefully);
  * ``inline_nli_gate`` suppresses evidence-contradicted sentences mid-stream;
  * ``gated_synthesis`` collects the gated stream;
  * ``verify_claims`` consolidates batch verification with error isolation;
  * the ``NLIUse`` / ``VerificationStatus`` enums.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── call_internal_llm_stream: routing + graceful degradation ────────────────

@pytest.mark.asyncio
async def test_stream_yields_local_tokens():
    from core.utils import internal_llm

    async def fake_stream(*_a, **_k):
        for tok in ["Hello ", "there ", "world."]:
            yield tok

    with patch.object(internal_llm, "_resolve_stage_provider", return_value="ollama"), \
         patch.object(internal_llm, "_stream_ollama", new=fake_stream):
        out = [
            t async for t in internal_llm.call_internal_llm_stream(
                [{"role": "user", "content": "hi"}], stage="test_stream",
            )
        ]
    assert "".join(out) == "Hello there world."


@pytest.mark.asyncio
async def test_stream_falls_back_when_local_fails_before_first_token():
    from core.utils import internal_llm

    async def fake_stream_fail(*_a, **_k):
        raise httpx.ConnectError("daemon down")
        yield  # pragma: no cover — makes this an async generator

    with patch.object(internal_llm, "_resolve_stage_provider", return_value="ollama"), \
         patch.object(internal_llm, "_stream_ollama", new=fake_stream_fail), \
         patch.object(internal_llm, "call_internal_llm", new=AsyncMock(return_value="full fallback answer")):
        out = [
            t async for t in internal_llm.call_internal_llm_stream(
                [{"role": "user", "content": "hi"}], stage="test_fb",
            )
        ]
    assert out == ["full fallback answer"]


@pytest.mark.asyncio
async def test_stream_partial_then_fail_does_not_duplicate():
    """A local failure AFTER partial output stops cleanly — no fallback re-emit."""
    from core.utils import internal_llm

    async def fake_stream_partial(*_a, **_k):
        yield "partial output "
        raise httpx.TimeoutException("mid-stream")

    fallback = AsyncMock(return_value="WHOLE DIFFERENT ANSWER")
    with patch.object(internal_llm, "_resolve_stage_provider", return_value="ollama"), \
         patch.object(internal_llm, "_stream_ollama", new=fake_stream_partial), \
         patch.object(internal_llm, "call_internal_llm", new=fallback):
        out = [
            t async for t in internal_llm.call_internal_llm_stream(
                [{"role": "user", "content": "hi"}], stage="test_partial",
            )
        ]
    assert out == ["partial output "]
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_stream_non_local_provider_single_chunk():
    from core.utils import internal_llm

    with patch.object(internal_llm, "_resolve_stage_provider", return_value="openrouter"), \
         patch.object(internal_llm, "call_internal_llm", new=AsyncMock(return_value="cloud answer")):
        out = [
            t async for t in internal_llm.call_internal_llm_stream(
                [{"role": "user", "content": "hi"}], stage="test_cloud",
            )
        ]
    assert out == ["cloud answer"]


@pytest.mark.asyncio
async def test_stream_stage_is_required_keyword():
    """stage is required kw-only — structurally satisfies the call-site contract."""
    import inspect

    from core.utils.internal_llm import call_internal_llm_stream

    sig = inspect.signature(call_internal_llm_stream)
    assert sig.parameters["stage"].default is inspect.Parameter.empty
    assert sig.parameters["stage"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.asyncio
async def test_stream_ollama_parses_ndjson():
    from core.utils import internal_llm

    class FakeResp:
        status_code = 200

        async def aiter_lines(self):
            yield '{"message":{"content":"Hel"},"done":false}'
            yield ""  # keepalive/blank line ignored
            yield '{"message":{"content":"lo"},"done":true}'

        async def aread(self):
            return b""

        def raise_for_status(self):
            return None

    class FakeCtx:
        async def __aenter__(self):
            return FakeResp()

        async def __aexit__(self, *_a):
            return False

    class FakeClient:
        def stream(self, _method, _url, json=None):
            return FakeCtx()

    with patch.object(internal_llm, "_get_ollama_client", new=AsyncMock(return_value=FakeClient())):
        out = [
            t async for t in internal_llm._stream_ollama(
                [{"role": "user", "content": "hi"}], temperature=0.1, max_tokens=10,
            )
        ]
    assert "".join(out) == "Hello"


# ── inline_nli_gate: mid-stream suppression ─────────────────────────────────

async def _stream_from(parts):
    for p in parts:
        yield p


@pytest.mark.asyncio
async def test_gate_suppresses_contradicted_sentence():
    from core.agents.hallucination.inline_gate import inline_nli_gate

    async def fake_nli(_premise, hypothesis):
        if "green" in hypothesis:
            return {"contradiction": 0.95, "entailment": 0.01, "neutral": 0.04, "label": "contradiction"}
        return {"contradiction": 0.02, "entailment": 0.9, "neutral": 0.08, "label": "entailment"}

    suppressed: list[str] = []
    with patch("core.agents.hallucination.inline_gate.nli_score_async", new=AsyncMock(side_effect=fake_nli)):
        out = [
            piece async for piece in inline_nli_gate(
                _stream_from(["The sky ", "is blue. ", "The sky ", "is green."]),
                context="The sky is blue.",
                on_suppress=lambda s, sc: suppressed.append(s),
            )
        ]
    result = "".join(out)
    assert "blue" in result
    assert "green" not in result
    assert suppressed == ["The sky is green."]


@pytest.mark.asyncio
async def test_gate_fails_open_on_nli_error():
    from core.agents.hallucination.inline_gate import inline_nli_gate

    with patch("core.agents.hallucination.inline_gate.nli_score_async", new=AsyncMock(side_effect=RuntimeError("nli down"))):
        out = [
            piece async for piece in inline_nli_gate(
                _stream_from(["Anything at all."]), context="some evidence",
            )
        ]
    assert "".join(out) == "Anything at all."


@pytest.mark.asyncio
async def test_gate_passthrough_without_context():
    from core.agents.hallucination.inline_gate import inline_nli_gate

    called = AsyncMock()
    with patch("core.agents.hallucination.inline_gate.nli_score_async", new=called):
        out = [
            piece async for piece in inline_nli_gate(
                _stream_from(["No evidence to check against."]), context="",
            )
        ]
    assert "".join(out) == "No evidence to check against."
    called.assert_not_called()  # empty premise → NLI never runs


@pytest.mark.asyncio
async def test_gated_synthesis_collects_gated_stream():
    from core.agents.hallucination import inline_gate

    async def fake_stream(*_a, **_k):
        for tok in ["Fact one is right. ", "Fact two is wrong."]:
            yield tok

    async def fake_nli(_premise, hypothesis):
        if "wrong" in hypothesis:
            return {"contradiction": 0.9, "entailment": 0.02, "neutral": 0.08, "label": "contradiction"}
        return {"contradiction": 0.01, "entailment": 0.95, "neutral": 0.04, "label": "entailment"}

    with patch("core.utils.internal_llm.call_internal_llm_stream", new=fake_stream), \
         patch.object(inline_gate, "nli_score_async", new=AsyncMock(side_effect=fake_nli)):
        answer = await inline_gate.gated_synthesis(
            [{"role": "user", "content": "q"}], context="Fact one is right.", stage="test_synth",
        )
    assert "Fact one is right." in answer
    assert "wrong" not in answer


# ── verify_claims facade + enums ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_claims_batches_and_isolates_errors():
    from core.agents.hallucination import verification
    from core.agents.hallucination.enums import VerificationStatus

    async def fake_verify_claim(claim, *_a, **_k):
        if claim == "boom":
            raise ValueError("verifier exploded")
        return {"claim": claim, "status": "verified", "confidence": 0.9}

    with patch.object(verification, "verify_claim", new=AsyncMock(side_effect=fake_verify_claim)):
        results = await verification.verify_claims(
            ["good claim", "boom", "another good"], chroma_client=None,
        )
    assert len(results) == 3
    assert results[0]["status"] == "verified"
    assert results[1]["status"] == VerificationStatus.error.value
    assert "verifier error" in results[1]["reason"]
    assert results[2]["status"] == "verified"


@pytest.mark.asyncio
async def test_verify_claims_empty_returns_empty():
    from core.agents.hallucination.verification import verify_claims

    assert await verify_claims([], chroma_client=None) == []


def test_verification_status_is_claim_status():
    from core.agents.hallucination.enums import NLIUse, VerificationStatus
    from core.agents.hallucination.models import ClaimStatus

    assert VerificationStatus is ClaimStatus
    assert VerificationStatus.verified.value == "verified"
    assert VerificationStatus.error.value == "error"
    # NLIUse names the entailment call sites (new, distinct enum).
    assert NLIUse.SYNTHESIS_GATE.value == "synthesis_gate"
    assert {u.value for u in NLIUse} >= {"synthesis_gate", "kb_gate", "cited_url"}
