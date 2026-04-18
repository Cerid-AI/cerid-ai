# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fact-level verification cache -- avoids re-verifying known facts.

Claims are normalized (lowercased, punctuation stripped, words sorted) so
semantically equivalent phrasings map to the same cache key. Verdicts are
stored in Redis with a 30-day TTL by default.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("ai-companion.claim_cache")

# Prefixes that indicate non-standard claims — skip caching for these
_SPECIAL_PREFIXES = ("[EVASION]", "[CITATION]", "[IGNORANCE]")

# ---------------------------------------------------------------------------
# L1 in-memory LRU cache — avoids Redis round-trip for recent claims
# ---------------------------------------------------------------------------
_L1_MAX_SIZE = 500
_L1_TTL = 300  # 5 minutes
_l1_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()


def _l1_get(key: str) -> dict[str, Any] | None:
    entry = _l1_cache.get(key)
    if entry is None:
        return None
    ts, verdict = entry
    if time.monotonic() - ts > _L1_TTL:
        _l1_cache.pop(key, None)
        return None
    _l1_cache.move_to_end(key)
    return verdict


def _l1_set(key: str, verdict: dict[str, Any]) -> None:
    _l1_cache[key] = (time.monotonic(), verdict)
    _l1_cache.move_to_end(key)
    while len(_l1_cache) > _L1_MAX_SIZE:
        _l1_cache.popitem(last=False)


def clear_l1_cache() -> None:
    """Clear the in-memory L1 cache. Used in tests to prevent cross-test leakage."""
    _l1_cache.clear()


def normalize_claim(claim: str) -> str:
    """Normalize a claim for deduplication.

    - lowercase
    - strip punctuation except apostrophes
    - collapse whitespace
    - preserve word order (sorting destroyed numeric/causal meaning)

    Word order is preserved because sorting causes false cache hits:
    "Python 3.12 was released before 3.11" and "Python 3.11 was released
    before 3.12" would sort to the same key despite opposite meanings.
    """
    text = claim.lower().strip()
    text = re.sub(r"[^\w\s']", "", text)  # keep apostrophes
    text = re.sub(r"\s+", " ", text).strip()
    return text


def claim_hash(claim: str) -> str:
    """SHA-256 hash (first 16 hex chars) of the normalized claim text.

    DEPRECATED: kept for backward compatibility only. Prefer
    :func:`claim_cache_key`, which additionally mixes in model, verification
    method, and response context so a claim verdict is not reused across model
    swaps or across conversations where a pronoun ("it") resolves to a
    different subject. This function is no longer used by the cache itself.
    """
    return hashlib.sha256(normalize_claim(claim).encode()).hexdigest()[:16]


# Bump this string when the cache key algorithm changes. Entries written under
# a prior schema will simply never be read — the old keys don't collide with
# the new `verf:claim:v2:…` namespace and will age out via their 30d TTL.
_CACHE_SCHEMA = "v2"


def claim_cache_key(
    claim: str,
    *,
    model: str,
    method: str,
    response_context: str,
) -> str:
    """Return a cache key mixing claim + model + method + response_context.

    Keying only on the claim text produces two failure modes:
      1. **Model swap staleness** — switching verification models silently
         returns the prior model's verdict even though the new model might
         reach a different conclusion.
      2. **Pronoun collision** — "It is 8848m tall" as a bare claim collides
         across conversations even when "it" resolves to different subjects.

    Mixing model, method, and the surrounding response context into the hash
    input eliminates both. The key is schema-versioned (``v2:``) so the v1
    cache can be left untouched — its entries will simply never be read and
    age out naturally.
    """
    normalized = normalize_claim(claim)
    # \x1f (Unit Separator) cannot appear in model IDs or method names and is
    # vanishingly unlikely in response text, so it's a safe field delimiter.
    material = (
        f"{_CACHE_SCHEMA}\x1f{model}\x1f{method}\x1f{normalized}\x1f"
        f"{response_context or ''}"
    )
    digest = hashlib.sha256(material.encode()).hexdigest()[:20]
    return f"{_CACHE_SCHEMA}:{digest}"


async def get_cached_verdict(
    redis_client,
    claim_text: str,
    *,
    model: str = "",
    method: str = "",
    response_context: str = "",
) -> dict[str, Any] | None:
    """Check if a claim has been verified before. Returns cached verdict or *None*.

    Uses a two-tier cache: L1 in-memory (5min TTL, ~500 entries) → L2 Redis (30d TTL).

    ``model``, ``method``, and ``response_context`` are folded into the key so
    that verdicts are scoped to the verification model, verification method,
    and conversational context in which they were produced. Call sites that
    don't yet pass them fall back to empty strings and operate in their own
    (distinct) cache namespace.
    """
    if any(claim_text.strip().startswith(p) for p in _SPECIAL_PREFIXES):
        return None
    key = f"verf:claim:{claim_cache_key(claim_text, model=model, method=method, response_context=response_context)}"
    # L1 check (no network I/O)
    l1_hit = _l1_get(key)
    if l1_hit is not None:
        logger.debug("Claim L1 cache hit: %s -> %s", key, l1_hit.get("status"))
        return l1_hit
    # L2 Redis check
    try:
        data = await asyncio.to_thread(redis_client.get, key)
        if data:
            verdict = json.loads(data)
            _l1_set(key, verdict)  # promote to L1
            logger.debug("Claim L2 cache hit: %s -> %s", key, verdict.get("status"))
            return verdict
    except Exception:
        logger.debug("Claim cache miss or error: %s", key)
    return None


async def cache_verdict(
    redis_client,
    claim_text: str,
    verdict: dict[str, Any],
    ttl: int = 2_592_000,
    response_context: str | None = None,
    *,
    model: str | None = None,
    method: str | None = None,
) -> None:
    """Cache a verified claim verdict. Default TTL: 30 days.

    The cache key is derived from ``(claim, model, method, response_context)``
    so verdicts don't bleed across model swaps or pronoun-different contexts.
    Callers that don't pass ``model`` / ``method`` land in the empty-string
    cache namespace, which pairs with ``get_cached_verdict`` callers that
    also don't pass them — this keeps backward compatibility with existing
    code paths while letting updated call sites scope keys per model/tier.

    The TTL shortening for web-search verdicts still uses the verdict
    payload's ``verification_method`` so time-sensitive data is always
    capped regardless of what the caller keys on.

    When ``response_context`` is provided it is also stored alongside the
    verdict so that future cache hits can include the topic context
    (e.g. "the Eiffel Tower") — enabling downstream consumers to interpret
    the cached claim correctly even when the bare claim text is ambiguous.
    """
    if any(claim_text.strip().startswith(p) for p in _SPECIAL_PREFIXES):
        return
    # Shorten TTL when the *verdict* is a web-search result — time-sensitive
    # data goes stale even if the reader asked us to scope the key differently.
    verdict_method = verdict.get("verification_method", "") or ""
    if verdict_method in ("web_search",) and ttl > 259_200:
        ttl = 259_200  # 3 days for web-search verdicts
    # Key components default to empty string so unupdated callers stay in a
    # self-consistent namespace with unupdated readers.
    key_model = model if model is not None else ""
    key_method = method if method is not None else ""
    key = f"verf:claim:{claim_cache_key(claim_text, model=key_model, method=key_method, response_context=response_context or '')}"
    try:
        cache_entry: dict[str, Any] = {
            "status": verdict.get("status", "unknown"),
            "similarity": verdict.get("similarity", 0),
            "verification_method": verdict.get("verification_method", ""),
            "verification_model": verdict.get("verification_model", ""),
            "reason": (verdict.get("reason") or "")[:200],
            "source_domain": verdict.get("source_domain", ""),
            "cached": True,
        }
        if response_context:
            cache_entry["response_context"] = response_context[:200]
        _l1_set(key, cache_entry)  # populate L1 immediately
        await asyncio.to_thread(redis_client.set, key, json.dumps(cache_entry), ttl)
        logger.debug("Claim cached: %s (status=%s, ttl=%d)", key, cache_entry["status"], ttl)
    except Exception as e:
        logger.debug("Failed to cache claim %s: %s", key, e)
