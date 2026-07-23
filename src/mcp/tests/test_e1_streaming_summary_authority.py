# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — streaming verification summary is a single source of truth.

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-104/107/113/115/067). The persisted verification report's summary was
maintained by side-effect across the main loop, ``_settle_timeouts``, and the
post-sweep recount, so it diverged from the claims array it shipped with:

* CR-107 — a ``status='error'`` claim vanished from every counter, so
  verified+unverified+uncertain+skipped < total.
* CR-067 — the persisted overall_score was the PRE-sweep mean while the counts
  were POST-sweep, so a sweep-resolved claim made the two disagree.
* CR-113 — the Redis ``hall:{cid}`` copy was serialized BEFORE the consistency
  fold-in, so it never carried ``consistency_issue`` while Neo4j/FE did.

The fix derives the persisted summary from ``_summarize_claims(run_claims)`` and
defers the Redis write to after the consistency fold-in. RED-then-GREEN.
"""
from __future__ import annotations

import contextlib
import json
from unittest.mock import AsyncMock, patch

import pytest

_MOD = "core.agents.hallucination.streaming"


class _FakeRedis:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def setex(self, key, _ttl, val):
        self.saved[key] = val

    def get(self, _key):
        return None

    def __getattr__(self, _name):
        return lambda *a, **k: None


@contextlib.contextmanager
def _extraction(claims: list[str]):
    """Force verify_response_streaming to extract exactly *claims* (heuristic)."""
    with (
        patch(f"{_MOD}._extract_claims_heuristic", return_value=claims),
        patch(f"{_MOD}._detect_evasion", return_value=[]),
        patch(f"{_MOD}._extract_citation_claims", return_value=[]),
        patch(f"{_MOD}._extract_ignorance_claims", return_value=[]),
        patch(f"{_MOD}._resolve_pronouns_heuristic", side_effect=lambda c, *a, **kw: c),
        patch(f"{_MOD}._extract_claims_llm", new_callable=AsyncMock, return_value=None),
    ):
        yield


async def _run(response_text, cid, redis, *, verify_claim, save=None, history=None, consistency=None):
    from core.agents.hallucination import verify_response_streaming

    patches = [
        patch(f"{_MOD}.verify_claim", side_effect=verify_claim),
        patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 5),
    ]
    if consistency is not None:
        patches.append(patch(f"{_MOD}._check_history_consistency", side_effect=consistency))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        events = []
        async for ev in verify_response_streaming(
            response_text, cid, None, None, redis,
            save_report_fn=save, conversation_history=history,
        ):
            events.append(ev)
    return events


@pytest.mark.asyncio
async def test_cr107_errored_claim_folds_into_total(monkeypatch):
    """An unresolved status='error' claim must count toward the total, not
    vanish. RED on HEAD: the recount excludes error, so counts < total."""
    captured: dict = {}

    async def _vc(claim_text, *a, **k):
        if "Error" in claim_text:
            return {"status": "error", "similarity": 0.0, "reason": "boom"}
        return {"status": "verified", "similarity": 0.8, "verification_method": "kb"}

    redis = _FakeRedis()
    with _extraction(["Verified fact.", "Error fact."]):
        await _run("A response with two facts.", "cid-107", redis,
                   verify_claim=_vc, save=lambda **kw: captured.update(kw))

    assert captured["verified"] == 1
    assert captured["uncertain"] == 1  # the errored claim folds into uncertain
    assert captured["verified"] + captured["unverified"] + captured["uncertain"] == captured["total"]


@pytest.mark.asyncio
async def test_cr067_persisted_overall_is_post_sweep(monkeypatch):
    """A claim resolved by the retry sweep must be reflected in the persisted
    overall_score. RED on HEAD: overall is the stale PRE-sweep mean while the
    counts are post-sweep, so verified=2 but overall ignores the swept claim."""
    captured: dict = {}
    calls = {"c2": 0}

    async def _vc(claim_text, *a, **k):
        if "one" in claim_text:
            return {"status": "verified", "similarity": 0.8, "verification_method": "kb"}
        calls["c2"] += 1
        if calls["c2"] == 1:
            # main-loop pass: times out (goes to the retry sweep)
            return {"status": "uncertain", "similarity": 0.0,
                    "verification_method": "timeout", "reason": "timed out"}
        # sweep pass: now resolves as verified
        return {"status": "verified", "similarity": 0.9, "verification_method": "kb"}

    redis = _FakeRedis()
    with _extraction(["Fact one.", "Fact two."]):
        await _run("A response with two facts here.", "cid-067", redis,
                   verify_claim=_vc, save=lambda **kw: captured.update(kw))

    assert captured["verified"] == 2
    # Post-sweep mean of 0.8 and 0.9 — NOT the pre-sweep 0.8.
    assert captured["overall_score"] == round((0.8 + 0.9) / 2, 3)


@pytest.mark.asyncio
async def test_cr113_redis_report_carries_consistency_issue(monkeypatch):
    """The durable Redis hall:{cid} report must include consistency_issue, like
    Neo4j/FE. RED on HEAD: Redis is serialized before the consistency fold-in."""
    from core.agents.hallucination.streaming import REDIS_HALLUCINATION_PREFIX

    async def _vc(claim_text, *a, **k):
        return {"status": "verified", "similarity": 0.7, "verification_method": "kb"}

    async def _consistency(_claims, _history):
        return [{"claim_index": 0, "contradiction": "contradicts an earlier turn"}]

    redis = _FakeRedis()
    with _extraction(["Fact one.", "Fact two."]):
        await _run("A response with two facts here.", "cid-113", redis,
                   verify_claim=_vc, history=[{"role": "user", "content": "x"}],
                   consistency=_consistency)

    report = json.loads(redis.saved[f"{REDIS_HALLUCINATION_PREFIX}cid-113"])
    assert report["claims"][0].get("consistency_issue") == "contradicts an earlier turn"
