# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verification pipeline self-test — runs at startup to confirm extraction works.

Fires a lightweight test response through claim extraction (no KB queries,
no external API calls) and records the result in Redis. The health dashboard
and `/health/status` endpoint surface this result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from errors import VerificationError

logger = logging.getLogger(__name__)

# A synthetic response with 3 obvious factual claims for extraction testing.
_TEST_RESPONSE = (
    "Python is a programming language created by Guido van Rossum. "
    "Neo4j is a graph database that uses the Cypher query language. "
    "Redis is an in-memory key-value store commonly used for caching."
)

_TEST_QUERY = "What technologies does the system use?"

# Redis keys
_SELF_TEST_KEY = "cerid:verification:self_test:last_result"
_FAILURE_KEY_PREFIX = "cerid:verification:failures:"
_FAILURE_TTL = 3600  # 1 hour — failed results expire faster, allowing natural recovery


async def run_verification_self_test(redis_client) -> dict:
    """Run a lightweight verification pipeline self-test.

    Only exercises claim extraction (not full verification against KB).
    Records result in Redis for the health dashboard.

    Returns dict with status, extraction_method, claims_found, duration_ms.
    """
    start = time.monotonic()
    result = {
        "status": "fail",
        "extraction_method": "none",
        "claims_found": 0,
        "duration_ms": 0.0,
        "timestamp": time.time(),
    }

    try:
        from core.agents.hallucination.extraction import extract_claims

        claims, method = await asyncio.wait_for(
            extract_claims(_TEST_RESPONSE, user_query=_TEST_QUERY),
            timeout=30.0,
        )

        result["extraction_method"] = method
        result["claims_found"] = len(claims)
        result["status"] = "pass" if len(claims) >= 1 else "fail"

    except asyncio.TimeoutError:
        result["status"] = "fail"
        result["extraction_method"] = "timeout"
        logger.warning("Verification self-test timed out after 30s")
    except (VerificationError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        result["status"] = "fail"
        result["extraction_method"] = "error"
        logger.warning("Verification self-test extraction error: %s", exc)

    elapsed_ms = (time.monotonic() - start) * 1000
    result["duration_ms"] = round(elapsed_ms, 1)

    # Persist result to Redis (fire-and-forget, non-blocking)
    try:
        if redis_client is not None:
            redis_client.set(_SELF_TEST_KEY, json.dumps(result), ex=3600)
    except (VerificationError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Failed to persist self-test result to Redis: %s", exc)

    return result


async def record_verification_failure(redis_client, failure_type: str) -> int:
    """Increment a consecutive failure counter in Redis. Returns new count."""
    key = f"{_FAILURE_KEY_PREFIX}{failure_type}"
    try:
        count = redis_client.incr(key)
        redis_client.expire(key, _FAILURE_TTL)
        return count
    except (VerificationError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        return -1


async def reset_verification_failures(redis_client, failure_type: str) -> None:
    """Clear a failure counter after a successful verification."""
    key = f"{_FAILURE_KEY_PREFIX}{failure_type}"
    try:
        redis_client.delete(key)
    except (VerificationError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        pass  # Self-test: graceful failure expected


def get_failure_counts_sync(redis_client) -> dict[str, int]:
    """Return all current verification failure counters (sync, for health endpoints)."""
    failure_types = ["extraction_timeout", "verification_timeout", "kb_unavailable"]
    counts: dict[str, int] = {}
    for ft in failure_types:
        key = f"{_FAILURE_KEY_PREFIX}{ft}"
        try:
            val = redis_client.get(key)
            if val is not None:
                counts[ft] = int(val)
        except (VerificationError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
            pass  # Self-test: graceful failure expected
    return counts


def get_self_test_status_sync(redis_client) -> dict | None:
    """Read the last self-test result from Redis (sync, for health endpoints)."""
    try:
        raw = redis_client.get(_SELF_TEST_KEY)
        if raw is not None:
            return json.loads(raw)
    except (VerificationError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        pass  # Self-test: graceful failure expected
    return None
