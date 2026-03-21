# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fact-level verification cache -- avoids re-verifying known facts.

Claims are normalized (lowercased, punctuation stripped, words sorted) so
semantically equivalent phrasings map to the same cache key. Verdicts are
stored in Redis with a 30-day TTL by default.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger("ai-companion.claim_cache")


def normalize_claim(claim: str) -> str:
    """Normalize a claim for deduplication.

    - lowercase
    - strip punctuation except apostrophes
    - collapse whitespace
    - sort words (order-independent: "capital of France" ~ "France's capital")
    """
    text = claim.lower().strip()
    text = re.sub(r"[^\w\s']", "", text)  # keep apostrophes
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(sorted(text.split()))


def claim_hash(claim: str) -> str:
    """SHA-256 hash (first 16 hex chars) of the normalized claim text."""
    return hashlib.sha256(normalize_claim(claim).encode()).hexdigest()[:16]


async def get_cached_verdict(redis_client, claim_text: str) -> dict[str, Any] | None:
    """Check if a claim has been verified before. Returns cached verdict or *None*."""
    key = f"verf:claim:{claim_hash(claim_text)}"
    try:
        data = await redis_client.get(key)
        if data:
            verdict = json.loads(data)
            logger.debug("Claim cache hit: %s -> %s", key, verdict.get("status"))
            return verdict
    except Exception:
        logger.debug("Claim cache miss or error: %s", key)
    return None


async def cache_verdict(
    redis_client,
    claim_text: str,
    verdict: dict[str, Any],
    ttl: int = 2_592_000,
) -> None:
    """Cache a verified claim verdict. Default TTL: 30 days."""
    key = f"verf:claim:{claim_hash(claim_text)}"
    try:
        cache_entry = {
            "status": verdict.get("status", "unknown"),
            "similarity": verdict.get("similarity", 0),
            "verification_method": verdict.get("verification_method", ""),
            "verification_model": verdict.get("verification_model", ""),
            "reason": (verdict.get("reason") or "")[:200],
            "source_domain": verdict.get("source_domain", ""),
            "cached": True,
        }
        await redis_client.set(key, json.dumps(cache_entry), ex=ttl)
        logger.debug("Claim cached: %s (status=%s)", key, cache_entry["status"])
    except Exception as e:
        logger.debug("Failed to cache claim %s: %s", key, e)
