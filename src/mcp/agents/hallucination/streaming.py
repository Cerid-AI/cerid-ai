# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination detection — streaming orchestration and batch verification.

Provides:
- ``check_hallucinations()`` — batch extraction + verification + Redis persistence
- ``verify_response_streaming()`` — streaming generator yielding results as they complete
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from typing import Any

import config
from agents.hallucination.extraction import extract_claims
from agents.hallucination.patterns import (
    _get_claim_verify_semaphore,
    _is_ignorance_admission,
)
from agents.hallucination.persistence import (
    REDIS_HALLUCINATION_PREFIX,
    REDIS_HALLUCINATION_TTL,
)
from agents.hallucination.verification import (
    _check_history_consistency,
    verify_claim,
)
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.hallucination")

_CGROUP_MEMORY_MAX = pathlib.Path("/sys/fs/cgroup/memory.max")
_CGROUP_MEMORY_CURRENT = pathlib.Path("/sys/fs/cgroup/memory.current")


def _container_memory_available_mb() -> float | None:
    """Return available memory in MB within the container cgroup, or None if not in a cgroup."""
    try:
        max_bytes = _CGROUP_MEMORY_MAX.read_text().strip()
        if max_bytes == "max":
            return None  # no limit set
        current_bytes = int(_CGROUP_MEMORY_CURRENT.read_text().strip())
        return (int(max_bytes) - current_bytes) / (1024 * 1024)
    except (FileNotFoundError, ValueError):
        return None  # not running in a cgroup-limited container


async def _wait_for_memory(floor_mb: int, label: str) -> None:
    """Block until available container memory exceeds floor_mb. No-op outside containers."""
    while True:
        available = _container_memory_available_mb()
        if available is None or available >= floor_mb:
            return
        logger.warning(
            "Verification paused (%s): container memory %.0fMB < %dMB floor",
            label, available, floor_mb,
        )
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------

async def check_hallucinations(
    response_text: str,
    conversation_id: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: float | None = None,
    model: str | None = None,
    user_query: str | None = None,
    expert_mode: bool = False,
) -> dict[str, Any]:
    """Extract claims, verify each against KB, and store results in Redis."""
    if threshold is None:
        threshold = config.HALLUCINATION_THRESHOLD
    min_length = config.HALLUCINATION_MIN_RESPONSE_LENGTH

    if len(response_text) < min_length:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "skipped": True,
            "reason": f"Response too short ({len(response_text)} chars < {min_length})",
            "claims": [],
            "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        }

    claims, method = await extract_claims(response_text, user_query=user_query)
    if not claims:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "skipped": True,
            "reason": "No factual claims extracted",
            "extraction_method": method,
            "claims": [],
            "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        }

    sem = _get_claim_verify_semaphore()

    async def _limited_verify(claim_text: str) -> dict[str, Any]:
        async with sem:
            return await verify_claim(
                claim_text, chroma_client, neo4j_driver, redis_client,
                threshold, model=model, expert_mode=expert_mode,
            )

    results = await asyncio.gather(*[_limited_verify(c) for c in claims])

    status_counts = {"verified": 0, "unverified": 0, "uncertain": 0, "error": 0}
    for r in results:
        status = r.get("status", "error")
        if status in status_counts:
            status_counts[status] += 1

    report = {
        "conversation_id": conversation_id,
        "timestamp": utcnow_iso(),
        "skipped": False,
        "threshold": threshold,
        "model": model,
        "extraction_method": method,
        "claims": list(results),
        "summary": {
            "total": len(results),
            **status_counts,
        },
    }

    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(report))
    except Exception as e:
        logger.warning("Failed to store hallucination report in Redis: %s", e)

    # Log verification metrics for analytics
    try:
        from utils.cache import log_verification_metrics
        used_models: list[str] = list({
            r.get("verification_model", "")
            for r in results if r.get("verification_model")
        })
        log_verification_metrics(
            redis_client,
            conversation_id=conversation_id,
            model=model,
            verified=status_counts["verified"],
            unverified=status_counts["unverified"],
            uncertain=status_counts["uncertain"],
            total=len(results),
            verification_models=used_models or None,
        )
    except Exception as e:
        logger.debug("Failed to log verification metrics (non-blocking): %s", e)

    return report


# ---------------------------------------------------------------------------
# Streaming orchestration
# ---------------------------------------------------------------------------

async def verify_response_streaming(
    response_text: str,
    conversation_id: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: float | None = None,
    model: str | None = None,
    user_query: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    expert_mode: bool = False,
):
    """Streaming verification generator — yields claim results as they are verified.

    Results are yielded as they complete (parallel execution), then persisted
    to Redis after the final summary for audit analytics and conversation revisits.

    When ``expert_mode`` is True, all claims are verified using the expert-tier
    model (Grok 4) instead of the default model pool.
    """
    if threshold is None:
        threshold = config.HALLUCINATION_THRESHOLD
    min_length = config.HALLUCINATION_MIN_RESPONSE_LENGTH

    if len(response_text) < min_length:
        yield {
            "type": "summary",
            "overall_confidence": 0,
            "verified": 0,
            "unverified": 0,
            "uncertain": 0,
            "total": 0,
            "skipped": True,
            "reason": f"Response too short ({len(response_text)} chars)",
        }
        return

    # Extraction with timeout — if Bifrost hangs or crashes, the generator
    # must still yield a summary instead of dying with an unhandled exception.
    EXTRACTION_TIMEOUT = 30  # seconds — Bifrost default is 20s
    try:
        claims, method = await asyncio.wait_for(
            extract_claims(response_text, user_query=user_query),
            timeout=EXTRACTION_TIMEOUT,
        )
    except TimeoutError:
        logger.error(
            "Claim extraction timed out after %ds for conversation %s",
            EXTRACTION_TIMEOUT, conversation_id,
        )
        claims, method = [], "timeout"
    except Exception as extraction_exc:
        logger.error(
            "Claim extraction failed for conversation %s: %s",
            conversation_id, extraction_exc,
        )
        claims, method = [], "error"

    if not claims:
        yield {
            "type": "summary",
            "overall_confidence": 0,
            "verified": 0,
            "unverified": 0,
            "uncertain": 0,
            "total": 0,
            "skipped": True,
            "reason": "No factual claims extracted" if method not in ("timeout", "error")
                      else f"Extraction {method}: could not extract claims",
            "extraction_method": method,
        }
        return

    # Classify each claim's type for frontend display
    def _claim_type(claim_text: str) -> str:
        if claim_text.startswith("[EVASION]"):
            return "evasion"
        if claim_text.startswith("[CITATION]"):
            return "citation"
        if _is_ignorance_admission(claim_text):
            return "ignorance"
        return "factual"

    # Notify frontend of extraction method and all extracted claims
    yield {"type": "extraction_complete", "method": method, "count": len(claims)}

    for i, claim in enumerate(claims):
        yield {
            "type": "claim_extracted",
            "claim": claim,
            "index": i,
            "claim_type": _claim_type(claim),
        }

    # --- Parallel verification via asyncio.as_completed ---
    verified_count = 0
    unverified_count = 0
    uncertain_count = 0
    assessed_confidence = 0.0  # Only accumulate for verified/unverified
    assessed_count = 0
    collected_results: list[dict[str, Any] | None] = [None] * len(claims)
    stream_interrupted = False

    async def _verify_indexed(idx: int, claim_text: str) -> tuple[int, dict[str, Any]]:
        """Verify a single claim with a per-claim timeout and concurrency limit."""
        await _wait_for_memory(config.VERIFY_MEMORY_FLOOR_MB, f"claim-{idx}")
        sem = _get_claim_verify_semaphore()
        try:
            async with sem:
                result = await asyncio.wait_for(
                    verify_claim(
                        claim_text, chroma_client, neo4j_driver, redis_client,
                        threshold, model=model, streaming=True,
                        expert_mode=expert_mode,
                    ),
                    timeout=config.STREAMING_PER_CLAIM_TIMEOUT,
                )
        except TimeoutError:
            logger.warning(
                "Claim %d verification timed out after %ds: '%s...'",
                idx, config.STREAMING_PER_CLAIM_TIMEOUT, claim_text[:50],
            )
            result = {
                "claim": claim_text,
                "status": "uncertain",
                "similarity": 0.0,
                "reason": f"Verification timed out ({int(config.STREAMING_PER_CLAIM_TIMEOUT)}s)",
                "verification_method": "timeout",
            }
        return idx, result

    tasks = [_verify_indexed(i, claim) for i, claim in enumerate(claims)]

    # Total deadline prevents the verification loop from running forever.
    # Individual claims have per-claim timeouts, but the total deadline
    # catches edge cases where many claims each take close to the limit.
    stream_deadline = time.monotonic() + config.STREAMING_TOTAL_TIMEOUT

    # Wrap verification loop in try/except to guarantee summary emission.
    # Without this, an unhandled exception (e.g., task cancellation, httpx
    # connection pool error) would terminate the async generator before the
    # summary event is yielded, causing the frontend to show "stream interrupted".
    try:
        for coro in asyncio.as_completed(tasks):
            # Check total deadline before awaiting the next result
            remaining = stream_deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "Streaming verification total timeout reached (%ds) "
                    "after %d/%d claims",
                    config.STREAMING_TOTAL_TIMEOUT,
                    verified_count + unverified_count + uncertain_count,
                    len(claims),
                )
                stream_interrupted = True
                # Count remaining uncompleted claims as uncertain
                completed = verified_count + unverified_count + uncertain_count
                uncertain_count += len(claims) - completed
                break
            try:
                i, result = await asyncio.wait_for(coro, timeout=remaining)
            except TimeoutError:
                logger.warning(
                    "Stream deadline expired waiting for claim result "
                    "(%ds total)", config.STREAMING_TOTAL_TIMEOUT,
                )
                stream_interrupted = True
                completed = verified_count + unverified_count + uncertain_count
                uncertain_count += len(claims) - completed
                break
            except Exception as task_exc:
                logger.warning("Verification task failed: %s", task_exc)
                try:
                    from utils.cache import log_verification_error
                    log_verification_error(
                        redis_client, conversation_id,
                        error_type="claim_verification_failed",
                        error_message=str(task_exc),
                        model=model, phase="verification",
                    )
                except Exception:
                    pass
                continue

            status = result.get("status", "error")
            confidence = result.get("similarity", 0.0)

            if status == "verified":
                verified_count += 1
                assessed_confidence += confidence
                assessed_count += 1
            elif status == "unverified":
                unverified_count += 1
                assessed_confidence += confidence
                assessed_count += 1
            else:
                # Uncertain/unassessable claims excluded from confidence avg
                uncertain_count += 1

            collected_results[i] = result

            yield {
                "type": "claim_verified",
                "index": i,
                "claim": claims[i],
                "claim_type": _claim_type(claims[i]),
                "status": status,
                "confidence": confidence,
                "source": result.get("source_filename", ""),
                "source_artifact_id": result.get("source_artifact_id", ""),
                "source_domain": result.get("source_domain", ""),
                "source_snippet": result.get("source_snippet", ""),
                "reason": result.get("reason", ""),
                "verification_method": result.get("verification_method", "kb"),
                "verification_model": result.get("verification_model"),
                "source_urls": result.get("source_urls", []),
                "verification_answer": result.get("verification_answer", ""),
            }
    except Exception as loop_exc:
        logger.error(
            "Verification loop interrupted after %d/%d claims: %s",
            verified_count + unverified_count + uncertain_count,
            len(claims),
            loop_exc,
        )
        stream_interrupted = True
        try:
            from utils.cache import log_verification_error
            log_verification_error(
                redis_client, conversation_id,
                error_type="stream_interrupted",
                error_message=str(loop_exc),
                model=model, phase="verification",
            )
        except Exception:
            pass

    # --- Consistency checking (cross-turn + internal contradictions) ---
    # Launch as a background task so it overlaps with summary emission and
    # report persistence, rather than blocking the stream sequentially.
    consistency_task: asyncio.Task[list[dict[str, Any]]] | None = None
    if not stream_interrupted and (conversation_history or len(claims) >= 2):
        consistency_task = asyncio.create_task(
            _check_history_consistency(claims, conversation_history)
        )

    # GUARANTEED summary emission — the frontend relies on receiving this event
    # to transition from "verifying" to "done".  Without it, the stream appears
    # interrupted and the UI shows an error.
    overall = (assessed_confidence / assessed_count) if assessed_count > 0 else 0
    yield {
        "type": "summary",
        "overall_confidence": round(overall, 3),
        "verified": verified_count,
        "unverified": unverified_count,
        "uncertain": uncertain_count,
        "total": len(claims),
        "assessed": assessed_count,
        "extraction_method": method,
        **({"interrupted": True} if stream_interrupted else {}),
    }

    # --- Persist to Redis (same format as batch path) ---
    status_counts = {
        "verified": verified_count,
        "unverified": unverified_count,
        "uncertain": uncertain_count,
    }
    report = {
        "conversation_id": conversation_id,
        "timestamp": utcnow_iso(),
        "skipped": False,
        "threshold": threshold,
        "model": model,
        "extraction_method": method,
        "claims": [r for r in collected_results if r is not None],
        "summary": {
            "total": len(claims),
            **status_counts,
        },
    }
    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(report))
    except Exception as e:
        logger.warning("Failed to persist streaming report to Redis: %s", e)

    try:
        from utils.cache import log_verification_metrics
        # Collect distinct verification models used across all claims
        used_models: list[str] = list({
            r.get("verification_model", "")
            for r in collected_results if r and r.get("verification_model")
        })
        log_verification_metrics(
            redis_client,
            conversation_id=conversation_id,
            model=model,
            verified=verified_count,
            unverified=unverified_count,
            uncertain=uncertain_count,
            total=len(claims),
            verification_models=used_models or None,
        )
    except Exception as e:
        logger.debug("Failed to log streaming verification metrics: %s", e)

    # --- Await consistency result (launched earlier as background task) ---
    if consistency_task is not None:
        try:
            consistency_issues = await asyncio.wait_for(consistency_task, timeout=15.0)
            if consistency_issues:
                # Annotate collected_results with consistency issues
                for issue in consistency_issues:
                    idx = issue.get("claim_index", -1)
                    if 0 <= idx < len(collected_results) and collected_results[idx] is not None:
                        collected_results[idx]["consistency_issue"] = issue.get("contradiction", "")
                yield {
                    "type": "consistency_check",
                    "issues": consistency_issues,
                }
                logger.info(
                    "Consistency check found %d issues for conversation %s",
                    len(consistency_issues),
                    conversation_id,
                )
        except TimeoutError:
            logger.warning("Consistency check timed out for conversation %s", conversation_id)
        except Exception as e:
            logger.warning("Consistency check failed: %s", e)
            try:
                from utils.cache import log_verification_error
                log_verification_error(
                    redis_client, conversation_id,
                    error_type="consistency_check_failed",
                    error_message=str(e),
                    model=model, phase="consistency",
                )
            except Exception:
                pass
