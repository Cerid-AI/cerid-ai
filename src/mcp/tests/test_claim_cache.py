# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for fact-level verification caching (utils/claim_cache.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from utils.claim_cache import (
    cache_verdict,
    claim_hash,
    get_cached_verdict,
    normalize_claim,
)

# ---------------------------------------------------------------------------
# normalize_claim
# ---------------------------------------------------------------------------

class TestNormalizeClaim:
    def test_basic_normalization(self):
        assert normalize_claim("  Hello   World  ") == "hello world"

    def test_strips_punctuation(self):
        result = normalize_claim("Paris is the capital of France!")
        assert "!" not in result

    def test_preserves_apostrophes(self):
        result = normalize_claim("France's capital")
        assert "france's" in result

    def test_order_independence(self):
        """Words are sorted, so reordered claims map to the same key."""
        a = normalize_claim("capital of France")
        b = normalize_claim("France capital of")
        assert a == b

    def test_case_insensitive(self):
        assert normalize_claim("PARIS") == normalize_claim("paris")

    def test_empty_string(self):
        assert normalize_claim("") == ""

    def test_only_punctuation(self):
        assert normalize_claim("!@#$%^&*()") == ""


# ---------------------------------------------------------------------------
# claim_hash
# ---------------------------------------------------------------------------

class TestClaimHash:
    def test_deterministic(self):
        h1 = claim_hash("The sky is blue")
        h2 = claim_hash("The sky is blue")
        assert h1 == h2

    def test_length(self):
        h = claim_hash("any claim")
        assert len(h) == 16

    def test_hex_chars(self):
        h = claim_hash("test claim")
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_claims_differ(self):
        h1 = claim_hash("The sky is blue")
        h2 = claim_hash("Water boils at 100 degrees")
        assert h1 != h2

    def test_normalized_equivalence(self):
        """Equivalent claims produce the same hash."""
        h1 = claim_hash("Capital of France")
        h2 = claim_hash("france capital of")
        assert h1 == h2


# ---------------------------------------------------------------------------
# get_cached_verdict
# ---------------------------------------------------------------------------

class TestGetCachedVerdict:
    @pytest.mark.asyncio
    async def test_cache_hit(self):
        verdict = {"status": "verified", "similarity": 0.95}
        redis = AsyncMock()
        redis.get.return_value = json.dumps(verdict)

        result = await get_cached_verdict(redis, "The sky is blue")
        assert result is not None
        assert result["status"] == "verified"

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        redis = AsyncMock()
        redis.get.return_value = None

        result = await get_cached_verdict(redis, "unknown claim")
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_error_returns_none(self):
        redis = AsyncMock()
        redis.get.side_effect = ConnectionError("connection refused")

        result = await get_cached_verdict(redis, "some claim")
        assert result is None


# ---------------------------------------------------------------------------
# cache_verdict
# ---------------------------------------------------------------------------

class TestCacheVerdict:
    @pytest.mark.asyncio
    async def test_stores_correctly(self):
        redis = AsyncMock()
        verdict = {
            "status": "verified",
            "similarity": 0.92,
            "verification_method": "kb",
            "verification_model": "grok-4",
            "reason": "Matched KB entry",
            "source_domain": "general",
        }

        await cache_verdict(redis, "test claim", verdict)

        redis.set.assert_called_once()
        call_args = redis.set.call_args
        key = call_args[0][0]
        stored = json.loads(call_args[0][1])

        assert key.startswith("verf:claim:")
        assert stored["status"] == "verified"
        assert stored["similarity"] == 0.92
        assert stored["cached"] is True

    @pytest.mark.asyncio
    async def test_respects_ttl(self):
        redis = AsyncMock()
        verdict = {"status": "verified"}

        await cache_verdict(redis, "test", verdict, ttl=3600)

        call_kwargs = redis.set.call_args[1]
        assert call_kwargs["ex"] == 3600

    @pytest.mark.asyncio
    async def test_default_ttl_30_days(self):
        redis = AsyncMock()
        verdict = {"status": "unverified"}

        await cache_verdict(redis, "test", verdict)

        call_kwargs = redis.set.call_args[1]
        assert call_kwargs["ex"] == 2_592_000

    @pytest.mark.asyncio
    async def test_truncates_long_reason(self):
        redis = AsyncMock()
        verdict = {"status": "verified", "reason": "x" * 500}

        await cache_verdict(redis, "test", verdict)

        stored = json.loads(redis.set.call_args[0][1])
        assert len(stored["reason"]) == 200

    @pytest.mark.asyncio
    async def test_redis_error_swallowed(self):
        redis = AsyncMock()
        redis.set.side_effect = ConnectionError("connection refused")

        # Should not raise
        await cache_verdict(redis, "test", {"status": "verified"})


# ---------------------------------------------------------------------------
# Round-trip: cache -> get
# ---------------------------------------------------------------------------

class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_cache_then_get(self):
        """Cached verdict is retrievable via get_cached_verdict."""
        store: dict[str, str] = {}

        async def mock_set(key, value, ex=None):
            store[key] = value

        async def mock_get(key):
            return store.get(key)

        redis = AsyncMock()
        redis.set = mock_set
        redis.get = mock_get

        original = {
            "status": "verified",
            "similarity": 0.88,
            "verification_method": "kb",
            "verification_model": None,
            "reason": "Strong KB match",
            "source_domain": "general",
        }

        await cache_verdict(redis, "Paris is the capital of France", original)
        result = await get_cached_verdict(redis, "Paris is the capital of France")

        assert result is not None
        assert result["status"] == "verified"
        assert result["cached"] is True

    @pytest.mark.asyncio
    async def test_equivalent_claims_share_cache(self):
        """Semantically equivalent claims (different word order) hit the same cache entry."""
        store: dict[str, str] = {}

        async def mock_set(key, value, ex=None):
            store[key] = value

        async def mock_get(key):
            return store.get(key)

        redis = AsyncMock()
        redis.set = mock_set
        redis.get = mock_get

        await cache_verdict(redis, "capital of France is Paris", {"status": "verified"})
        result = await get_cached_verdict(redis, "Paris is capital of France")

        assert result is not None
        assert result["status"] == "verified"
