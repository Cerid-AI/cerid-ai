# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase 1 trust-integrity regression tests (2026-07-13 quality program §1.1-1.3).

Guards the leaks that survived the same-day anti-circularity/deadline fixes:

1.1 The ``verify_claims`` facade (used by briefs) must forward the new
    integrity params (deadline / stale_context / source_artifact_ids /
    claim_context) and per-claim source_urls to ``verify_claim`` instead of
    silently bypassing them.
1.2 The kb_batch fast path must require NLI entailment before pre-resolving —
    relevance + substring alone is the "shared keywords, different topic"
    false positive the slow path's semantic-alignment gate refuses.
1.3 Verification-promoted memories are inadmissible as verification evidence
    (``_query_memories`` where-clause exclusion), empirical memories decay on
    a finite curve, and the +0.25 authority boost no longer applies to
    verification-source memories (self-reinforcement loop).
"""

import contextlib
import math
import time
from unittest.mock import patch

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


async def _collect_events(response_text, claims, kb_results, verify_claim_mock, extra_ctx=()):
    """Drive verify_response_streaming with mocked extraction/KB/verify."""
    from core.agents.hallucination import verify_response_streaming

    async def _mock_kb(query, domains=None, top_k=5, chroma_client=None, **kw):
        return kb_results

    ctx = [
        _mock_streaming_extraction(claims, method="heuristic"),
        patch(f"{_QUERY_AGENT}.lightweight_kb_query", side_effect=_mock_kb),
        patch(f"{_STREAMING_MOD}.verify_claim", side_effect=verify_claim_mock),
        patch("config.STREAMING_TOTAL_TIMEOUT", 10),
        patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 10),
        *extra_ctx,
    ]
    events = []
    with contextlib.ExitStack() as stack:
        for c in ctx:
            stack.enter_context(c)
        async for event in verify_response_streaming(
            response_text, "test-trust-p1", None, None, None,
        ):
            events.append(event)
    return events


# ---------------------------------------------------------------------------
# 1.1 — verify_claims facade forwards the integrity params
# ---------------------------------------------------------------------------

class TestFacadeForwarding:
    """The batch facade must not be a bypass around verify_claim's gates."""

    @pytest.mark.asyncio
    async def test_facade_forwards_integrity_params_and_source_urls(self):
        from core.agents.hallucination import verification as v

        captured: list[dict] = []

        async def _fake_verify_claim(claim, *a, **kw):
            captured.append({"claim": claim, **kw})
            return {"claim": claim, "status": "verified", "confidence": 0.9,
                    "verification_method": "cross_model"}

        deadline = time.monotonic() + 30.0
        with patch.object(v, "verify_claim", side_effect=_fake_verify_claim):
            await v.verify_claims(
                ["Paris is the capital of France",
                 "See http://example.com/doc for details"],
                None,
                deadline=deadline,
                stale_context=True,
                source_artifact_ids=["art-1"],
                claim_context="surrounding text",
            )

        assert len(captured) == 2, "facade did not verify every claim"
        for call in captured:
            assert call["deadline"] == deadline
            assert call["stale_context"] is True
            assert call["source_artifact_ids"] == ["art-1"]
            assert call["claim_context"] == "surrounding text"

        by_claim = {c["claim"]: c for c in captured}
        assert by_claim[
            "See http://example.com/doc for details"
        ]["source_urls"] == ["http://example.com/doc"], "cited URL not extracted per-claim"
        assert by_claim["Paris is the capital of France"]["source_urls"] == []


# ---------------------------------------------------------------------------
# 1.2 — kb_batch pre-resolution requires NLI entailment
# ---------------------------------------------------------------------------

class TestKbBatchNliGate:
    """Relevance + substring is not enough — the fast path must NLI-entail."""

    @pytest.mark.asyncio
    async def test_low_entailment_rejects_preresolution(self):
        claim = "The project uses PostgreSQL for the finance store"

        async def _verify(*a, **kw):
            return {"status": "uncertain", "similarity": 0.3,
                    "verification_method": "cross_model"}

        async def _low_nli(premise, hypothesis):
            return {"entailment": 0.05, "contradiction": 0.10,
                    "neutral": 0.85, "label": "neutral"}

        events = await _collect_events(
            "The project uses PostgreSQL for the finance store as noted.",
            [claim], [_kb_result(claim, "notes")], _verify,
            extra_ctx=[patch("core.utils.nli.nli_score_async", side_effect=_low_nli)],
        )
        cv = [e for e in events if e["type"] == "claim_verified"]
        assert cv, "no claim_verified event"
        assert cv[0]["verification_method"] != "kb_batch", (
            "claim pre-resolved on lexical overlap despite low NLI entailment"
        )

    @pytest.mark.asyncio
    async def test_high_entailment_allows_preresolution(self):
        """Positive control — a genuinely entailed claim still pre-resolves."""
        claim = "The project uses PostgreSQL for the finance store"

        async def _verify(*a, **kw):  # only runs if pre-resolution failed
            return {"status": "uncertain", "similarity": 0.3,
                    "verification_method": "cross_model"}

        async def _high_nli(premise, hypothesis):
            return {"entailment": 0.95, "contradiction": 0.01,
                    "neutral": 0.04, "label": "entailment"}

        events = await _collect_events(
            "The project uses PostgreSQL for the finance store as noted.",
            [claim], [_kb_result(claim, "notes")], _verify,
            extra_ctx=[patch("core.utils.nli.nli_score_async", side_effect=_high_nli)],
        )
        cv = [e for e in events if e["type"] == "claim_verified"]
        assert cv and cv[0]["verification_method"] == "kb_batch"
        assert cv[0]["status"] == "verified"


# ---------------------------------------------------------------------------
# 1.3a — _query_memories excludes verification-promoted memories
# ---------------------------------------------------------------------------

def _contains_ne_verification(node) -> bool:
    """Recursively search a Chroma where-dict for the verification exclusion."""
    if isinstance(node, dict):
        if node.get("memory_source_type") == {"$ne": "verification"}:
            return True
        return any(_contains_ne_verification(val) for val in node.values())
    if isinstance(node, list):
        return any(_contains_ne_verification(item) for item in node)
    return False


class TestQueryMemoriesExcludesVerification:
    """A prior verdict must never be re-served as verification evidence."""

    @pytest.mark.asyncio
    async def test_where_clause_excludes_verification_source(self):
        from core.agents.hallucination import verification as v

        captured: dict = {}

        class _Coll:
            def query(self, **kw):
                captured["where"] = kw.get("where")
                return {"ids": [[]], "documents": [[]],
                        "metadatas": [[]], "distances": [[]]}

        class _Client:
            def get_collection(self, name):
                return _Coll()

        await v._query_memories("some claim", _Client())

        where = captured.get("where")
        assert where is not None, "no where clause passed to Chroma"
        assert _contains_ne_verification(where), (
            f"where clause does not exclude memory_source_type==verification: {where}"
        )


# ---------------------------------------------------------------------------
# 1.3b — empirical memories decay on a finite curve
# ---------------------------------------------------------------------------

class TestEmpiricalTtlFinite:
    def test_empirical_stability_is_finite(self):
        import config
        assert math.isfinite(config.MEMORY_TYPE_STABILITY["empirical"]), (
            "empirical memory stability is infinite — verification-promoted "
            "facts would never decay (self-reinforcement loop)"
        )


# ---------------------------------------------------------------------------
# 1.3c — authority boost no longer privileges verification-source memories
# ---------------------------------------------------------------------------

class TestAuthorityBoostCapsVerification:
    def test_verification_source_demoted_below_tier1(self):
        from core.agents.hallucination.patterns import memory_authority_boost

        boost = memory_authority_boost({
            "memory_type": "empirical",
            "memory_source_type": "verification",
            "_raw_relevance": 0.9,
        })
        assert boost < 0.25, (
            f"verification-promoted memory still gets the Tier-1 boost: {boost}"
        )

    def test_genuine_user_empirical_keeps_tier1(self):
        from core.agents.hallucination.patterns import memory_authority_boost

        boost = memory_authority_boost({
            "memory_type": "empirical",
            "_raw_relevance": 0.9,
        })
        assert boost == 0.25, "genuine user empirical fact was wrongly demoted"
