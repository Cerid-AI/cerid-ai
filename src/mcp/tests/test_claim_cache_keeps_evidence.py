# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A cached verdict keeps the evidence it rests on.

The claim cache stored status, method and similarity, and dropped the source
artifact, filename and URLs. A KB verdict served from the cache had no source
the user could open, and could not be labelled "KB verified" against the next
answer's sources: live 2026-10-10, the second verification of the same claim
came back verified with an empty source_artifact_id.
"""
from __future__ import annotations

import pytest

from core.agents.hallucination.verification import label_answer_source
from core.utils.claim_cache import cache_verdict, clear_l1_cache, get_cached_verdict

CLAIM = "Dollar-cost averaging invests a fixed amount at regular intervals."
VERDICT = {"claim": CLAIM, "status": "verified", "similarity": 0.9, "verification_method": "kb",
           "source_artifact_id": "88409ab9", "source_filename": "dca.md",
           "source_urls": ["https://example.org/dca"]}


class _DictRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key, value, ttl=None):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("from_redis", [False, True])
async def test_a_cached_verdict_keeps_its_evidence(from_redis):
    redis = _DictRedis()
    clear_l1_cache()
    await cache_verdict(redis, CLAIM, VERDICT)
    if from_redis:
        clear_l1_cache()

    cached = await get_cached_verdict(redis, CLAIM)

    assert cached["source_artifact_id"] == "88409ab9"
    assert cached["source_filename"] == "dca.md"
    assert cached["source_urls"] == ["https://example.org/dca"]
    assert label_answer_source(cached, ["88409ab9"])["circular_source"] is True


@pytest.mark.asyncio
async def test_a_verdict_never_crosses_a_consumer_wall():
    """The entry names its source artifact, so a verdict reached against the
    owner's whole knowledge base must not reach a consumer walled off from it."""
    from core.agents.hallucination import scope

    redis = _DictRedis()
    clear_l1_cache()
    await cache_verdict(redis, CLAIM, VERDICT, partition=scope.cache_partition())

    token = scope._scope.set(scope.VerificationScope(domains=frozenset({"finance"}), memory=False))
    try:
        walled = await get_cached_verdict(redis, CLAIM, partition=scope.cache_partition())
    finally:
        scope._scope.reset(token)
    owner = await get_cached_verdict(redis, CLAIM, partition=scope.cache_partition())

    assert walled is None
    assert owner["source_artifact_id"] == "88409ab9"


@pytest.mark.asyncio
async def test_the_verifier_reads_and_writes_the_cache_in_its_scope(monkeypatch, mock_chroma, mock_neo4j, mock_redis):

    from core.agents.hallucination import scope, verification

    seen: dict[str, str] = {}

    async def fake_get(_redis, _claim, **kw):
        seen["read"] = kw["partition"]
        return dict(VERDICT)

    monkeypatch.setattr(verification, "get_cached_verdict", fake_get)
    token = scope._scope.set(scope.VerificationScope(domains=frozenset({"finance"}), memory=False))
    try:
        await verification.verify_claim(CLAIM, mock_chroma[0], mock_neo4j[0], mock_redis)
        expected = scope.cache_partition()
    finally:
        scope._scope.reset(token)

    assert seen["read"] == expected and "finance" in expected
