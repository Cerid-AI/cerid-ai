# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-2e verifiability harness — SINGLE-CLAIM-RETRY MERGE probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 2.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-019, CONFIRMED / high).

The FE per-claim "retry" re-runs ONE claim of an N-claim report through the same
``/agent/verify-stream`` endpoint (``response_text`` is just that claim, under the
original ``conversation_id``). Before the fix that run persisted UNCONDITIONALLY —
the Redis ``hall:{cid}`` setex + the Neo4j ``save_report_fn`` both REPLACED the
N-claim report with a 1-claim report, destroying the other claims' verdicts and
invalidating their feedback indices (a subsequent thumbs on claim 5 → 400
"Invalid claim index"; the durable record unrecoverable).

The fix threads ``merge_claim_index`` into ``verify_response_streaming``: the fresh
verdict is MERGED into ``claims[index]`` of the existing durable report (summary
recomputed), and if there is no existing report / the index is out of range the
durable persist is SKIPPED rather than clobbering it.

This probe drives the REAL generator with a pre-seeded 4-claim report in a fake
Redis + a capturing Neo4j save_report_fn, and asserts the merge preserves the
other claims in BOTH stores. RED-then-GREEN; GREEN -> preservation gates.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import core.agents.hallucination.streaming as streaming
from core.agents.hallucination.persistence import REDIS_HALLUCINATION_PREFIX

_CID = "e1-retry"
_RETRY_CLAIM = "The mitochondria is the powerhouse of the cell."
_RESPONSE = _RETRY_CLAIM + " " * 40  # comfortably above the min-length gate

# A pre-existing 4-claim report — the durable record a retry must NOT destroy.
_EXISTING_CLAIMS = [
    {"claim": "Claim zero.", "status": "verified", "similarity": 0.9,
     "verification_method": "kb", "source_filename": "a.md"},
    {"claim": "Claim one.", "status": "unverified", "similarity": 0.2,
     "verification_method": "kb", "source_filename": "b.md"},
    {"claim": _RETRY_CLAIM, "status": "uncertain", "similarity": 0.0,
     "verification_method": "timeout", "source_filename": "",
     "user_feedback": "correct"},
    {"claim": "Claim three.", "status": "verified", "similarity": 0.8,
     "verification_method": "kb", "source_filename": "d.md"},
]
_EXISTING_REPORT = {
    "conversation_id": _CID,
    "claims": _EXISTING_CLAIMS,
    "summary": {"total": 4, "verified": 2, "unverified": 1, "uncertain": 1, "skipped": 0},
}


class _SeededRedis:
    """Fake redis pre-seeded with the existing hall:{cid} report; captures setex."""

    def __init__(self, seeded: dict | None):
        self._store: dict[str, str] = {}
        if seeded is not None:
            self._store[f"{REDIS_HALLUCINATION_PREFIX}{_CID}"] = json.dumps(seeded)
        self.setex_calls: list[tuple[str, str]] = []

    def get(self, key):
        return self._store.get(key)

    def setex(self, key, ttl, payload):
        self._store[key] = payload
        self.setex_calls.append((key, payload))

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
    return [_RETRY_CLAIM], "heuristic"


_FRESH_VERDICT = {
    "claim": _RETRY_CLAIM,
    "status": "verified",
    "similarity": 0.97,
    "verification_method": "expert",
    "verification_model": "grok-4",
    "source_filename": "fresh-source.md",
    "reason": "expert re-verification",
}


async def _drive_retry(monkeypatch, seeded, merge_index):
    """Drive the real generator as a single-claim retry. Returns (redis, neo4j_calls)."""
    monkeypatch.setattr(streaming, "extract_claims", _fake_extract)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic", lambda t: [_RETRY_CLAIM])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic", lambda c, *a, **k: c)

    async def _fresh_verify(*a, **k):
        return dict(_FRESH_VERDICT)

    monkeypatch.setattr(streaming, "verify_claim", _fresh_verify)

    neo4j_calls: list[dict] = []

    def _save_report_fn(**kwargs):
        neo4j_calls.append(kwargs)

    redis = _SeededRedis(seeded)
    async for _ev in streaming.verify_response_streaming(
        response_text=_RESPONSE,
        conversation_id=_CID,
        chroma_client=MagicMock(),
        neo4j_driver=MagicMock(),
        redis_client=redis,
        save_report_fn=_save_report_fn,
        merge_claim_index=merge_index,
    ):
        pass
    return redis, neo4j_calls


def _persisted_report(redis: _SeededRedis) -> dict:
    assert redis.setex_calls, "generator never persisted a hall:{cid} report"
    return json.loads(redis.setex_calls[-1][1])


@pytest.mark.preservation
async def test_retry_merges_fresh_verdict_and_keeps_other_claims(monkeypatch):
    """A retry of claim 2 must leave a 4-claim report with only claim 2 updated —
    in BOTH Redis and the Neo4j payload. RED on HEAD (CR-019): the run replaced the
    report with a 1-claim report."""
    redis, neo4j_calls = await _drive_retry(monkeypatch, _EXISTING_REPORT, merge_index=2)

    report = _persisted_report(redis)
    claims = report["claims"]
    assert len(claims) == 4, (
        f"retry clobbered the N-claim report — persisted {len(claims)} claim(s), "
        "the other claims' verdicts + feedback indices are gone (CR-019)"
    )
    # Claim 2 carries the fresh verdict...
    assert claims[2]["status"] == "verified"
    assert claims[2]["verification_method"] == "expert"
    assert claims[2]["source_filename"] == "fresh-source.md"
    # ...prior human feedback on it is preserved (re-verify refreshes the verdict,
    # not the thumbs signal)...
    assert claims[2].get("user_feedback") == "correct"
    # ...and the OTHER claims are byte-for-byte the originals.
    assert claims[0] == _EXISTING_CLAIMS[0]
    assert claims[1] == _EXISTING_CLAIMS[1]
    assert claims[3] == _EXISTING_CLAIMS[3]
    # Summary recomputed and consistent with the merged claims (was uncertain=1,
    # now the retried claim flipped uncertain→verified).
    assert report["summary"]["total"] == 4
    assert report["summary"]["verified"] == 3
    assert report["summary"]["uncertain"] == 0

    # Neo4j got the merged 4-claim report too, not a 1-claim overwrite.
    assert neo4j_calls, "Neo4j save_report_fn was never invoked"
    assert neo4j_calls[-1]["total"] == 4
    assert len(neo4j_calls[-1]["claims"]) == 4
    assert neo4j_calls[-1]["verified"] == 3


@pytest.mark.preservation
async def test_retry_with_bad_index_does_not_clobber(monkeypatch):
    """A retry whose index is out of range for the existing report must SKIP the
    durable persist rather than replace it with a 1-claim report. RED on HEAD
    (CR-019): the run persisted a 1-claim report regardless."""
    redis, neo4j_calls = await _drive_retry(monkeypatch, _EXISTING_REPORT, merge_index=9)

    # Redis still holds the original 4-claim report — nothing was overwritten.
    stored = json.loads(redis._store[f"{REDIS_HALLUCINATION_PREFIX}{_CID}"])
    assert len(stored["claims"]) == 4, "bad-index retry clobbered the durable report"
    assert not redis.setex_calls, "bad-index retry wrote a report (should skip persist)"
    # And the Neo4j writer was NOT invoked with a 1-claim overwrite.
    assert not neo4j_calls, "bad-index retry persisted to Neo4j (should skip)"


@pytest.mark.preservation
async def test_retry_with_no_existing_report_does_not_create_one(monkeypatch):
    """A retry with no existing durable report must not create a 1-claim report
    out of thin air (it would be a spurious record with a wrong feedback space)."""
    redis, neo4j_calls = await _drive_retry(monkeypatch, None, merge_index=0)

    assert not redis.setex_calls, "retry created a durable report with no base report"
    assert not neo4j_calls, "retry persisted to Neo4j with no base report"
