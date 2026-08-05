# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Regression tests for the 2026-07-13 verification-quality fixes.

Live-proven failure modes these guard against:

1. Circular verification — the batch KB pre-fetch queried ALL domains
   (including ``conversations``), so a hallucinated claim got "verified"
   against the assistant's own prior transcript once it was ingested.
2. Time-sensitive bypass — kb_batch pre-resolution accepted recency/
   ignorance-typed claims, skipping the staleness gates and forced web
   checks in ``verify_claim``.
3. Stale-cutoff framing lost — "as of my knowledge cutoff…" appears at
   response level but is stripped from extracted claims, so the claims
   were verified by static cross-model verifiers with stale cutoffs.
4. Timeout inversion — inner LLM calls were granted more time (20s/40s)
   than the outer per-claim budget (18s/25s), guaranteeing mid-flight
   timeouts; sequential fallback chains never fit the budget.
5. Sweep-resolved verdicts were persisted but never re-emitted, so the
   UI showed stale timeout verdicts that disagreed with the saved report.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_verify_stream import _mock_streaming_extraction

_STREAMING_MOD = "core.agents.hallucination.streaming"
_QUERY_AGENT = "core.agents.query_agent"


def _kb_result(claim: str, domain: str, relevance: float = 0.95, **extra):
    """A lightweight_kb_query result whose content embeds the claim text."""
    return {
        "content": f"Source: doc | Domain: {domain}\n\n{claim} — and more context.",
        "relevance": relevance,
        "artifact_id": extra.pop("artifact_id", f"art-{domain}"),
        "filename": f"doc_{domain}",
        "domain": domain,
        **extra,
    }


async def _collect_events(response_text: str, claims: list[str], kb_results, verify_claim_mock, **patches):
    """Drive verify_response_streaming with mocked extraction/KB/verify."""
    from core.agents.hallucination import verify_response_streaming

    async def _mock_kb(query, domains=None, top_k=5, chroma_client=None, **kw):
        _mock_kb.captured_domains = domains
        return kb_results

    _mock_kb.captured_domains = "NOT_CALLED"

    ctx = [
        _mock_streaming_extraction(claims, method="heuristic"),
        patch(f"{_QUERY_AGENT}.lightweight_kb_query", side_effect=_mock_kb),
        patch(f"{_STREAMING_MOD}.verify_claim", side_effect=verify_claim_mock),
        patch("config.STREAMING_TOTAL_TIMEOUT", patches.pop("total_timeout", 10)),
        patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
    ]
    events = []
    import contextlib
    with contextlib.ExitStack() as stack:
        for c in ctx:
            stack.enter_context(c)
        async for event in verify_response_streaming(
            response_text, "test-quality-001", None, None, None,
        ):
            events.append(event)
    return events, _mock_kb


class TestBatchPrefetchAntiCircularity:
    """RC1 — the batch KB pre-fetch must mirror the per-claim domain rules."""

    @pytest.mark.asyncio
    async def test_batch_prefetch_excludes_conversations_domain(self):
        claim = "Paris is the capital of France"

        async def _verify(*a, **kw):
            return {"status": "verified", "similarity": 0.9,
                    "verification_method": "cross_model"}

        _, mock_kb = await _collect_events(
            "Paris is the capital of France. It hosts the Eiffel Tower.",
            [claim], [], _verify,
        )
        assert mock_kb.captured_domains != "NOT_CALLED", "batch pre-fetch did not run"
        assert mock_kb.captured_domains is not None, (
            "batch pre-fetch passed domains=None → queries ALL domains "
            "including conversations (circular verification)"
        )
        assert "conversations" not in mock_kb.captured_domains

    @pytest.mark.asyncio
    async def test_kb_batch_never_preresolves_from_conversations(self):
        """Defense in depth: even if a conversations-domain result appears
        in the batch context, pre-resolution must not use it."""
        claim = "No woman has traveled around the Moon"

        async def _verify(*a, **kw):
            return {"status": "uncertain", "similarity": 0.4,
                    "verification_method": "cross_model"}

        events, _ = await _collect_events(
            f"{claim}. That is what the assistant said last time.",
            [claim],
            [_kb_result(claim, "conversations")],
            _verify,
        )
        cv = [e for e in events if e["type"] == "claim_verified"]
        assert cv, "no claim_verified event"
        assert cv[0]["verification_method"] != "kb_batch", (
            "claim pre-resolved against a prior chat transcript"
        )

    @pytest.mark.asyncio
    async def test_kb_batch_preresolves_plain_factual_from_kb(self):
        """Positive control — the fast path must still work for static
        factual claims backed by a real KB artifact."""
        claim = "The project uses PostgreSQL for the finance store"

        async def _verify(*a, **kw):  # would only run if pre-resolution failed
            return {"status": "uncertain", "similarity": 0.3,
                    "verification_method": "cross_model"}

        events, _ = await _collect_events(
            "The project uses PostgreSQL for the finance store as noted.",
            [claim],
            [_kb_result(claim, "notes")],
            _verify,
        )
        cv = [e for e in events if e["type"] == "claim_verified"]
        assert cv and cv[0]["verification_method"] == "kb_batch"
        assert cv[0]["status"] == "verified"

    @pytest.mark.asyncio
    async def test_kb_batch_skips_stale_cutoff_response(self):
        """RC3 — a response admitting a stale knowledge cutoff must not have
        its claims confirmed from KB snapshots."""
        claim = "No woman has traveled around the Moon"

        async def _verify(*a, **kw):
            return {"status": "uncertain", "similarity": 0.4,
                    "verification_method": "web_search"}

        events, _ = await _collect_events(
            "As of my knowledge cutoff in 2023, no woman has traveled "
            "around the Moon.",
            [claim],
            [_kb_result(claim, "notes")],
            _verify,
        )
        cv = [e for e in events if e["type"] == "claim_verified"]
        assert cv, "no claim_verified event"
        assert cv[0]["verification_method"] != "kb_batch", (
            "stale-cutoff response claim was confirmed from a KB snapshot"
        )


class TestStaleResponseWebRouting:
    """RC3 — stale-cutoff responses route factual claims to the batched
    web check."""

    @pytest.mark.asyncio
    async def test_stale_response_factual_claims_join_web_batch(self):
        claims = [
            "No woman has traveled around the Moon",
            "The Artemis II mission has not launched",
        ]
        captured: dict = {}

        async def _mock_batch(batch_claims, **kw):
            captured["claims"] = list(batch_claims)
            return {}

        async def _verify(*a, **kw):
            return {"status": "uncertain", "similarity": 0.4,
                    "verification_method": "web_search"}

        from core.agents.hallucination import verify_response_streaming
        with (
            _mock_streaming_extraction(claims, method="heuristic"),
            patch(f"{_QUERY_AGENT}.lightweight_kb_query", new=AsyncMock(return_value=[])),
            patch(
                "core.agents.hallucination.verification.verify_claims_batch_external",
                side_effect=_mock_batch,
            ),
            patch(f"{_STREAMING_MOD}.verify_claim", side_effect=_verify),
            patch("config.STREAMING_TOTAL_TIMEOUT", 10),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
        ):
            async for _ in verify_response_streaming(
                "As of my knowledge cutoff in 2023, no woman has traveled "
                "around the Moon and Artemis II has not launched.",
                "test-quality-002", None, None, None,
            ):
                pass

        assert captured.get("claims"), (
            "stale-cutoff factual claims were not routed to the batched "
            "web pre-verification"
        )
        assert [c for _, c in captured["claims"]] == claims


class TestStaleContextForcesWeb:
    """RC3 — stale-cutoff context must survive into verify_claim's own
    external fallbacks, not just the batched pre-verify (the individual
    path can win the race against the batch)."""

    @pytest.mark.asyncio
    async def test_stale_context_forces_web_on_no_kb_fallback(self):
        from core.agents.hallucination import verification as v

        captured: dict = {}

        async def _fake_ext(claim, *a, **kw):
            captured["force_web_search"] = kw.get("force_web_search", False)
            return {"status": "unverified", "confidence": 0.2,
                    "reason": "outdated", "verification_method": "web_search",
                    "source_urls": ["https://example.org/a"]}

        with (
            patch.object(v, "_verify_claim_externally", side_effect=_fake_ext),
            patch("core.agents.query_agent.lightweight_kb_query",
                  new=AsyncMock(return_value=[])),
        ):
            result = await v.verify_claim(
                "No woman has traveled around the Moon",
                None, None, None,
                stale_context=True,
            )
        assert captured.get("force_web_search") is True, (
            "stale-cutoff claim fell back to a static cross-model check"
        )
        assert result["status"] == "unverified"


class TestDeadlineThreading:
    """RC4 — inner budgets must fit the caller's per-claim window."""

    @pytest.mark.asyncio
    async def test_exhausted_deadline_skips_external_call(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        llm = AsyncMock(side_effect=AssertionError("LLM must not be called"))
        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", llm),
        ):
            verdict = await _verify_claim_externally(
                "The sky is blue", deadline=time.monotonic() + 1.0,
            )
        assert verdict["status"] == "uncertain"
        assert verdict["verification_method"] == "timeout"
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_timeout_clamped_to_remaining_budget(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        captured: dict = {}

        async def _fake_llm(messages, **kw):
            captured["timeout"] = kw.get("timeout")
            return {"choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "ok"}',
                "annotations": [],
            }}]}

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", side_effect=_fake_llm),
        ):
            verdict = await _verify_claim_externally(
                "Bitcoin's current price is above $100k",
                force_web_search=True,
                deadline=time.monotonic() + 10.0,
            )
        assert verdict["status"] == "verified"
        # Web calls default to BIFROST_TIMEOUT*2 (40s); with 10s of budget
        # left the call must be granted less than the remaining window.
        assert captured["timeout"] is not None
        assert captured["timeout"] <= 10.0

    @pytest.mark.asyncio
    async def test_no_deadline_keeps_default_timeouts(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        captured: dict = {}

        async def _fake_llm(messages, **kw):
            captured["timeout"] = kw.get("timeout")
            return {"choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "ok"}',
                "annotations": [],
            }}]}

        import config as _config
        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", side_effect=_fake_llm),
        ):
            await _verify_claim_externally("The sky is blue")
        assert captured["timeout"] == _config.BIFROST_TIMEOUT


class TestSweepReemission:
    """RC5 — sweep-resolved verdicts must reach the frontend."""

    @pytest.mark.asyncio
    async def test_sweep_reemits_claim_and_summary_update(self):
        from core.agents.hallucination import verify_response_streaming

        calls = {"n": 0}

        async def _verify(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("per-claim timeout")
            return {"status": "verified", "similarity": 0.88,
                    "verification_method": "cross_model",
                    "reason": "resolved on retry"}

        with (
            _mock_streaming_extraction(["Paris is the capital of France."], method="heuristic"),
            patch(f"{_QUERY_AGENT}.lightweight_kb_query", new=AsyncMock(return_value=[])),
            patch(f"{_STREAMING_MOD}.verify_claim", side_effect=_verify),
            patch("config.STREAMING_TOTAL_TIMEOUT", 10),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
        ):
            events = []
            async for event in verify_response_streaming(
                "Test response for sweep re-emission",
                "test-quality-003", None, None, None,
            ):
                events.append(event)

        types = [e["type"] for e in events]
        summary_pos = types.index("summary")
        post_summary_cv = [
            e for e in events[summary_pos + 1:] if e["type"] == "claim_verified"
        ]
        assert post_summary_cv, "sweep-resolved verdict was not re-emitted"
        assert post_summary_cv[0]["status"] == "verified"
        assert "summary_update" in types[summary_pos + 1:]
        update = next(e for e in events if e["type"] == "summary_update")
        assert update["verified"] == 1
        assert update["total"] == 1


class TestExternalEscalationConfidenceThreading:
    """RC6 — external-escalation return paths dropped the ``similarity`` field.

    ``verify_claim``'s NLI-entailment / NLI-contradiction / semantic-alignment
    escalations returned the raw ``_verify_claim_externally`` result, which
    scores under ``confidence`` (not ``similarity``). The ``claim_verified`` SSE
    event reads ``result["similarity"]`` for its ``confidence`` field, so
    terminal verified/unverified verdicts on these paths reached the frontend
    with confidence=0.0 despite a definitive status (calibration cases V-94,
    TS-03). Every external-escalation verdict must now carry
    ``similarity == the verifier's confidence`` while preserving the full
    external payload the audit UI shows.
    """

    @staticmethod
    def _ext_verified():
        return {
            "status": "verified",
            "confidence": 0.82,
            "reason": "Cross-model verification confirmed: known fact.",
            "verification_method": "cross_model",
            "verification_model": "openrouter/test",
            "source_urls": [],
            "verification_answer": "Concrete verifier answer.",
        }

    async def _run_semantic_gate(self, ext_return):
        from unittest.mock import MagicMock

        from core.agents.hallucination.verification import verify_claim

        claim = "Docker was written in Java"
        kb = [{
            "content": "Docker is a containerization platform. "
                       "Java is a programming language.",
            "relevance": 0.90,
            "artifact_id": "a1",
            "filename": "doc.md",
            "domain": "coding",
        }]

        async def _kb(*a, **k):
            return kb

        async def _ext(*a, **k):
            return ext_return

        # NLI neutral with entailment below the 0.15 semantic-alignment floor
        # forces the high-similarity KB verdict to escalate externally.
        neutral_nli = {
            "entailment": 0.05, "contradiction": 0.05,
            "neutral": 0.90, "label": "neutral",
        }
        with (
            patch(f"{_QUERY_AGENT}.lightweight_kb_query", side_effect=_kb),
            patch("core.agents.hallucination.verification._query_memories",
                  new=AsyncMock(return_value=[])),
            patch("core.utils.nli.nli_score_async",
                  new=AsyncMock(return_value=neutral_nli)),
            patch("core.agents.hallucination.verification._verify_claim_externally",
                  side_effect=_ext),
            patch("core.agents.hallucination.verification.get_cached_verdict",
                  new=AsyncMock(return_value=None)),
            patch("core.agents.hallucination.verification.cache_verdict",
                  new=AsyncMock(return_value=None)),
        ):
            return await verify_claim(claim, MagicMock(), None, MagicMock())

    @pytest.mark.asyncio
    async def test_semantic_gate_escalation_threads_similarity(self):
        result = await self._run_semantic_gate(self._ext_verified())
        assert result["status"] == "verified"
        assert result.get("kb_semantic_gate_escalated") is True
        # The defect: without the field, the SSE confidence defaulted to 0.0.
        assert result["similarity"] == 0.82
        # Escalation verdicts keep the full external payload for the audit UI.
        assert result["verification_answer"] == "Concrete verifier answer."

    @pytest.mark.asyncio
    async def test_semantic_gate_escalation_sse_confidence_nonzero(self):
        """Field contract: the value the ``claim_verified`` SSE event publishes
        as ``confidence`` is exactly ``result.get("similarity", 0.0)`` (see
        ``streaming._claim_verified_event``). On the escalation path it must
        equal the verifier's confidence, never fall back to the 0.0 default."""
        result = await self._run_semantic_gate(self._ext_verified())
        sse_confidence = result.get("similarity", 0.0)  # mirrors the SSE builder
        assert sse_confidence == 0.82, (
            "escalation verdict lacked `similarity` → SSE confidence=0.0"
        )
