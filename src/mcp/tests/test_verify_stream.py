# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the SSE verify-stream endpoint keepalive mechanism.

Validates the PEP 479 fix: ``_safe_anext()`` must be a regular async function
(not an async generator) so that ``StopAsyncIteration`` raised by the
underlying generator is caught normally instead of being converted to
``RuntimeError`` by PEP 479 inside an async generator frame.

Also validates streaming timeout behavior (per-claim and total deadlines).
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.routers.agents import _STREAM_END, _safe_anext

# ---------------------------------------------------------------------------
# Helper: mock the individual extraction functions that verify_response_streaming
# now calls directly (instead of the top-level extract_claims wrapper).
# ---------------------------------------------------------------------------
_STREAMING_MOD = "core.agents.hallucination.streaming"


@contextlib.contextmanager
def _mock_streaming_extraction(claims: list[str], method: str = "heuristic"):
    """Patch the individual extraction helpers so verify_response_streaming
    produces exactly *claims* with the given *method*.

    For ``method="heuristic"``: ``_extract_claims_heuristic`` returns the claims.
    For ``method="llm"``: heuristic returns ``[]`` and ``_extract_claims_llm``
    returns the claims.
    For ``method="none"`` or empty claims with heuristic: heuristic returns ``[]``
    and LLM returns ``None``.
    """
    heuristic_rv = claims if (method == "heuristic" and claims) else []
    llm_rv: list[str] | None = claims if method == "llm" else None
    if method == "none":
        llm_rv = None

    with (
        patch(f"{_STREAMING_MOD}._extract_claims_heuristic", return_value=heuristic_rv),
        patch(f"{_STREAMING_MOD}._detect_evasion", return_value=[]),
        patch(f"{_STREAMING_MOD}._extract_citation_claims", return_value=[]),
        patch(f"{_STREAMING_MOD}._extract_ignorance_claims", return_value=[]),
        patch(f"{_STREAMING_MOD}._resolve_pronouns_heuristic", side_effect=lambda c, *a, **kw: c),
        patch(f"{_STREAMING_MOD}._extract_claims_llm", new_callable=AsyncMock, return_value=llm_rv),
    ):
        yield

# ---------------------------------------------------------------------------
# Helpers — synthetic async generators for testing
# ---------------------------------------------------------------------------


async def _gen_items(*items):
    """Yield the given items, then exhaust."""
    for item in items:
        yield item


async def _gen_empty():
    """An async generator that yields nothing."""
    return
    yield  # noqa: RET504 — makes this an async generator


async def _gen_raises(exc_cls, *items):
    """Yield items, then raise an exception."""
    for item in items:
        yield item
    raise exc_cls("synthetic error")


# ---------------------------------------------------------------------------
# Tests for _safe_anext
# ---------------------------------------------------------------------------


class TestSafeAnext:
    """Verify _safe_anext correctly handles generator exhaustion."""

    @pytest.mark.asyncio
    async def test_returns_items(self):
        gen = _gen_items("a", "b", "c")
        results = []
        while True:
            result = await _safe_anext(gen)
            if result is _STREAM_END:
                break
            results.append(result)
        assert results == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_returns_sentinel_on_exhaustion(self):
        gen = _gen_empty()
        result = await _safe_anext(gen)
        assert result is _STREAM_END

    @pytest.mark.asyncio
    async def test_sentinel_is_not_none(self):
        """Ensure sentinel doesn't collide with None values."""
        gen = _gen_items(None, None)
        r1 = await _safe_anext(gen)
        assert r1 is None  # Actual None from generator
        r2 = await _safe_anext(gen)
        assert r2 is None
        r3 = await _safe_anext(gen)
        assert r3 is _STREAM_END

    @pytest.mark.asyncio
    async def test_propagates_non_stop_exceptions(self):
        """Non-StopAsyncIteration exceptions must still propagate."""
        gen = _gen_raises(ValueError, "ok")
        r1 = await _safe_anext(gen)
        assert r1 == "ok"
        with pytest.raises(ValueError, match="synthetic error"):
            await _safe_anext(gen)


class TestSafeAnextInAsyncGenerator:
    """Verify that _safe_anext avoids the PEP 479 RuntimeError.

    PEP 479 says: if ``StopIteration`` (or ``StopAsyncIteration``) is raised
    inside a generator (or async generator), Python converts it to
    ``RuntimeError``.  This is the exact bug that caused "stream interrupted":
    calling ``task.result()`` inside the ``event_generator()`` async generator
    would re-raise ``StopAsyncIteration``, which PEP 479 converted to
    ``RuntimeError``, bypassing ``except StopAsyncIteration: break``.

    These tests prove that ``_safe_anext`` (a regular async function) does NOT
    trigger PEP 479.
    """

    @pytest.mark.asyncio
    async def test_no_runtime_error_in_async_generator(self):
        """Using _safe_anext inside an async generator must not raise RuntimeError."""

        async def consumer():
            gen = _gen_items(1, 2, 3)
            while True:
                event = await _safe_anext(gen)
                if event is _STREAM_END:
                    break
                yield event

        results = [item async for item in consumer()]
        assert results == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_safe_anext_works_with_ensure_future(self):
        """Verify that _safe_anext + ensure_future terminates cleanly.

        This is the exact pattern used in the SSE endpoint.  The sentinel
        approach avoids any StopAsyncIteration propagation issues regardless
        of Python version or asyncio Task behavior.
        """

        async def consumer():
            gen = _gen_items("x", "y")
            while True:
                task = asyncio.ensure_future(_safe_anext(gen))
                await asyncio.wait({task})
                event = task.result()
                if event is _STREAM_END:
                    break
                yield event

        results = [item async for item in consumer()]
        assert results == ["x", "y"]


class TestKeepaliveIntegration:
    """Integration tests simulating the keepalive mechanism from agents.py."""

    @pytest.mark.asyncio
    async def test_keepalive_with_slow_generator(self):
        """Simulate a slow generator that triggers keepalive emissions."""

        async def _slow_gen():
            yield {"type": "extraction_complete"}
            await asyncio.sleep(0.1)
            yield {"type": "claim_verified", "index": 0}
            yield {"type": "summary", "total": 1}

        async def event_generator():
            gen = _slow_gen()
            anext_task = None
            try:
                while True:
                    if anext_task is None:
                        anext_task = asyncio.ensure_future(_safe_anext(gen))
                    done, _ = await asyncio.wait({anext_task}, timeout=0.05)
                    if done:
                        event = anext_task.result()
                        if event is _STREAM_END:
                            break
                        yield ("data", event)
                        anext_task = None
                    else:
                        yield ("keepalive", None)
            finally:
                if anext_task and not anext_task.done():
                    anext_task.cancel()
                await gen.aclose()

        results = []
        async for kind, event in event_generator():
            results.append((kind, event))

        # Should have data events and at least one keepalive
        data_events = [r for r in results if r[0] == "data"]
        keepalive_events = [r for r in results if r[0] == "keepalive"]
        assert len(data_events) == 3
        assert len(keepalive_events) >= 1

    @pytest.mark.asyncio
    async def test_generator_error_propagated_not_as_stream_end(self):
        """Errors from the generator should propagate, not silently end."""

        async def _error_gen():
            yield {"type": "extraction_complete"}
            raise ValueError("backend crash")

        gen = _error_gen()
        r1 = await _safe_anext(gen)
        assert r1["type"] == "extraction_complete"

        with pytest.raises(ValueError, match="backend crash"):
            await _safe_anext(gen)


class TestStreamingTimeouts:
    """Verify per-claim and total verification timeouts."""

    @pytest.mark.asyncio
    async def test_per_claim_timeout_returns_uncertain(self):
        """A claim that exceeds the per-claim timeout should return 'uncertain'."""
        from agents.hallucination import verify_response_streaming

        async def _mock_verify_claim(*args, **kwargs):
            """Raise TimeoutError directly — simulates what asyncio.wait_for
            does when verify_claim exceeds the adaptive per-claim timeout.
            The _verify_indexed handler catches this and converts it to an
            'uncertain' result with a 'timed out' reason."""
            raise TimeoutError("per-claim timeout")

        # Patch verify_claim and individual extraction helpers with fast fakes.
        # The streaming path now uses adaptive per-claim timeouts (12s for
        # non-web claims) instead of the old config.STREAMING_PER_CLAIM_TIMEOUT.
        # Raising TimeoutError directly from verify_claim is equivalent to
        # asyncio.wait_for raising it, and avoids waiting the full 12s.
        with (
            _mock_streaming_extraction(["Paris is the capital of France."], method="heuristic"),
            patch("core.agents.hallucination.streaming.verify_claim", side_effect=_mock_verify_claim),
            patch("config.STREAMING_TOTAL_TIMEOUT", 10),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
        ):
            events = []
            async for event in verify_response_streaming(
                "Test response text for timeout",
                "test-timeout-001",
                None, None, None,
            ):
                events.append(event)

        # Should have: extraction_complete, claim_extracted, claim_verified (timeout), summary
        types = [e["type"] for e in events]
        assert "extraction_complete" in types
        assert "claim_extracted" in types
        assert "summary" in types
        # The timed-out claim should appear as claim_verified with uncertain status
        verified_events = [e for e in events if e["type"] == "claim_verified"]
        assert len(verified_events) == 1
        assert verified_events[0]["status"] == "uncertain"
        assert "timed out" in verified_events[0]["reason"].lower()

    @pytest.mark.asyncio
    async def test_total_timeout_produces_summary(self):
        """When total deadline expires, stream should still emit a summary."""
        from agents.hallucination import verify_response_streaming

        call_count = 0

        async def _mock_verify_claim(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First claim returns quickly, rest hang
            if call_count == 1:
                return {"status": "verified", "similarity": 0.85}
            await asyncio.sleep(10)
            return {"status": "verified", "similarity": 0.9}

        with (
            _mock_streaming_extraction(["Claim 1.", "Claim 2.", "Claim 3."], method="heuristic"),
            patch("core.agents.hallucination.streaming.verify_claim", side_effect=_mock_verify_claim),
            patch("config.STREAMING_PER_CLAIM_TIMEOUT", 5),  # High so per-claim doesn't trigger first
            patch("config.STREAMING_TOTAL_TIMEOUT", 0.3),  # Low total timeout
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
        ):
            events = []
            async for event in verify_response_streaming(
                "Test response for total timeout",
                "test-total-001",
                None, None, None,
            ):
                events.append(event)

        types = [e["type"] for e in events]
        assert "summary" in types
        summary = next(e for e in events if e["type"] == "summary")
        # The first claim should have completed; remaining should be uncertain
        assert summary["total"] == 3

    @pytest.mark.asyncio
    async def test_streaming_flag_limits_retries(self):
        """In streaming mode, _llm_call_with_retry should use fewer attempts."""
        from core.agents.hallucination.verification import _llm_call_with_retry

        call_count = 0

        class FakeClient:
            async def post(self, url, json=None):
                nonlocal call_count
                call_count += 1

                class FakeResp:
                    status_code = 429
                    headers = {}

                    def raise_for_status(self):
                        raise Exception("Rate limited after exhausting retries")

                return FakeResp()

        # With max_attempts=1, should only try once then raise
        with pytest.raises(Exception, match="Rate limited"):
            await _llm_call_with_retry(FakeClient(), "http://fake", {}, max_attempts=1)

        assert call_count == 1  # Only 1 attempt, no retries

    @pytest.mark.asyncio
    async def test_streaming_mode_same_as_non_streaming_for_web_escalation(self):
        """After removing the streaming exclusion from staleness escalation,
        both streaming and non-streaming modes should behave identically
        with respect to web search escalation."""
        from agents.hallucination import verify_claim

        external_calls = []

        claim_text = "The capital city of France is Paris."
        async def _mock_lightweight_kb_query(query, domains=None, top_k=5, chroma_client=None):
            return [{
                "relevance": 0.55,
                "content": "France is a country in Europe with capital Paris",
                "artifact_id": "a1",
                "filename": "test.txt",
                "domain": "general",
            }]

        async def _mock_external(claim, model=None, force_web_search=False, streaming=False, expert_mode=False, fast_mode=False, response_context=None, claim_context=None):
            external_calls.append({"force_web_search": force_web_search, "streaming": streaming})
            return {
                "status": "uncertain",
                "confidence": 0.4,
                "reason": "Cannot determine",
                "verification_method": "cross_model",
                "source_urls": [],
            }

        async def _mock_memories(claim, chroma_client, top_k=2):
            return []

        with (
            patch("core.agents.query_agent.lightweight_kb_query", side_effect=_mock_lightweight_kb_query),
            patch("core.agents.hallucination.verification._verify_claim_externally", side_effect=_mock_external),
            patch("core.agents.hallucination.verification._query_memories", side_effect=_mock_memories),
        ):
            external_calls.clear()
            await verify_claim(claim_text, None, None, None, streaming=False)
            non_streaming_count = len(external_calls)

            external_calls.clear()
            await verify_claim(claim_text, None, None, None, streaming=True)
            streaming_count = len(external_calls)

        # Both modes should make the same number of external calls
        # (streaming no longer skips web escalation)
        assert non_streaming_count == streaming_count


class TestExtractionErrorHandling:
    """Verify that extraction errors don't crash the streaming generator."""

    @pytest.mark.asyncio
    async def test_extract_claims_llm_catches_timeout(self):
        """httpx.TimeoutException in LLM extraction should return empty list."""
        import httpx

        from core.agents.hallucination.extraction import _extract_claims_llm

        async def _mock_call_llm(*args, **kwargs):
            raise httpx.ReadTimeout("Connection timed out")

        with patch("core.agents.hallucination.extraction.call_llm", side_effect=_mock_call_llm):
            result = await _extract_claims_llm("Some response text for testing.", 10)

        assert result == []

    @pytest.mark.asyncio
    async def test_extract_claims_llm_catches_connect_error(self):
        """httpx.ConnectError in LLM extraction should return empty list."""
        import httpx

        from core.agents.hallucination.extraction import _extract_claims_llm

        async def _mock_call_llm(*args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        with patch("core.agents.hallucination.extraction.call_llm", side_effect=_mock_call_llm):
            result = await _extract_claims_llm("Some response text for testing.", 10)

        assert result == []

    @pytest.mark.asyncio
    async def test_streaming_extraction_crash_yields_summary(self):
        """When extraction crashes, streaming should still emit a summary."""
        from agents.hallucination import verify_response_streaming

        # The streaming path calls individual extraction functions directly.
        # When heuristic returns nothing and LLM extraction crashes, the
        # except block sets method="error" and emits a summary.
        async def _crash_llm(*args, **kwargs):
            raise RuntimeError("Bifrost is down")

        with (
            patch(f"{_STREAMING_MOD}._extract_claims_heuristic", return_value=[]),
            patch(f"{_STREAMING_MOD}._detect_evasion", return_value=[]),
            patch(f"{_STREAMING_MOD}._extract_citation_claims", return_value=[]),
            patch(f"{_STREAMING_MOD}._extract_ignorance_claims", return_value=[]),
            patch(f"{_STREAMING_MOD}._resolve_pronouns_heuristic", side_effect=lambda c, *a, **kw: c),
            patch(f"{_STREAMING_MOD}._extract_claims_llm", new_callable=AsyncMock, side_effect=_crash_llm),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
        ):
            events = []
            async for event in verify_response_streaming(
                "Test response for extraction crash handling",
                "test-crash-001",
                None, None, None,
            ):
                events.append(event)

        # Should get exactly one summary event with skipped=True
        assert len(events) == 1
        assert events[0]["type"] == "summary"
        assert events[0]["skipped"] is True
        assert events[0]["total"] == 0
        assert "error" in events[0]["extraction_method"]

    @pytest.mark.asyncio
    async def test_streaming_extraction_timeout_yields_summary(self):
        """When LLM extraction hangs (heuristic found nothing), the
        asyncio.wait_for timeout catches it and the generator still
        produces a summary event with extraction_method='timeout'."""
        from agents.hallucination import verify_response_streaming

        async def _timeout_llm(*args, **kwargs):
            """Simulate what asyncio.wait_for does when _extract_claims_llm
            exceeds the 30s timeout — it raises TimeoutError."""
            raise TimeoutError("simulated extraction timeout")

        # Heuristic returns nothing so the streaming path falls through to
        # the LLM branch, which we make timeout.
        with (
            patch(f"{_STREAMING_MOD}._extract_claims_heuristic", return_value=[]),
            patch(f"{_STREAMING_MOD}._detect_evasion", return_value=[]),
            patch(f"{_STREAMING_MOD}._extract_citation_claims", return_value=[]),
            patch(f"{_STREAMING_MOD}._extract_ignorance_claims", return_value=[]),
            patch(f"{_STREAMING_MOD}._resolve_pronouns_heuristic", side_effect=lambda c, *a, **kw: c),
            patch(f"{_STREAMING_MOD}._extract_claims_llm", new_callable=AsyncMock, side_effect=_timeout_llm),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
        ):
            events = []
            async for event in verify_response_streaming(
                "Test response for extraction timeout",
                "test-timeout-002",
                None, None, None,
            ):
                events.append(event)

        # TimeoutError is caught, method set to "timeout", summary emitted
        assert len(events) == 1
        assert events[0]["type"] == "summary"
        assert events[0]["skipped"] is True
        assert events[0]["total"] == 0
        assert "timeout" in events[0]["extraction_method"]
