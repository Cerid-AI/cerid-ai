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
import re
import time
from typing import Any

import config
from agents.hallucination.extraction import _reclassify_recency, extract_claims
from agents.hallucination.patterns import (
    _get_claim_verify_semaphore,
    _is_ignorance_admission,
    _is_recency_claim,
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


def _heuristic_response_context(response_text: str, user_query: str | None) -> str | None:
    """Heuristic fallback: build topic context from user query + first heading."""
    parts: list[str] = []
    if user_query:
        parts.append(user_query.strip()[:200])

    heading_match = re.search(r"^#{1,3}\s+(.+)", response_text, re.MULTILINE)
    if heading_match:
        heading = heading_match.group(1).strip()
        if heading and heading not in (user_query or ""):
            parts.append(heading[:100])

    if not parts:
        # Last resort: first non-empty line (likely the topic)
        for line in response_text.split("\n"):
            stripped = line.strip().lstrip("#").strip()
            if len(stripped) > 10:
                parts.append(stripped[:120])
                break

    return "; ".join(parts) if parts else None


async def _extract_response_context(response_text: str, user_query: str | None) -> str | None:
    """Build a brief topic summary for claim verification context.

    Attempts LLM-based extraction via the internal LLM (Ollama if available,
    else lightweight OpenRouter model) for a precise one-line topic summary.
    Falls back to heuristic extraction (user query + heading) on failure.
    """
    # Fast heuristic first — always available as fallback
    heuristic = _heuristic_response_context(response_text, user_query)

    # Try LLM-based extraction for higher quality context
    try:
        from utils.internal_llm import call_internal_llm

        snippet = response_text[:800]
        query_hint = f'\nUser asked: "{user_query}"' if user_query else ""
        prompt = (
            f"What is the main topic of this response? "
            f"Reply with ONLY a brief noun phrase (e.g. 'the Eiffel Tower', "
            f"'Python async programming', '2023 US tax filing'). "
            f"No explanation.\n\n{snippet}{query_hint}"
        )
        result = await asyncio.wait_for(
            call_internal_llm(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=40,
            ),
            timeout=5.0,
        )
        topic = result.strip().strip('"').strip("'").strip(".")
        if topic and 3 < len(topic) < 150:
            logger.debug("LLM topic extraction: '%s'", topic)
            return topic
    except Exception as exc:
        logger.debug("LLM topic extraction failed (%s), using heuristic", exc)

    return heuristic


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
    source_artifact_ids: list[str] | None = None,
):
    """Streaming verification generator — yields claim results as they are verified.

    Results are yielded as they complete (parallel execution), then persisted
    to Redis after the final summary for audit analytics and conversation revisits.

    When ``expert_mode`` is True, all claims are verified using the expert-tier
    model (Grok 4) instead of the default model pool.

    When ``source_artifact_ids`` is provided, KB results matching those IDs are
    penalised during confidence scoring to prevent circular self-verification
    (the KB confirming claims that were originally derived from it).
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
    # On timeout/error, fall back to heuristic extraction so claims are still
    # produced even when the LLM is unreachable.
    EXTRACTION_TIMEOUT = 30  # seconds — local Ollama needs 5-15s, cloud APIs 3-12s
    try:
        claims, method = await asyncio.wait_for(
            extract_claims(response_text, user_query=user_query),
            timeout=EXTRACTION_TIMEOUT,
        )
    except TimeoutError:
        logger.error(
            "Claim extraction timed out after %ds for conversation %s — "
            "falling back to heuristic",
            EXTRACTION_TIMEOUT, conversation_id,
        )
        claims, method = [], "timeout"
    except Exception as extraction_exc:
        logger.error(
            "Claim extraction failed for conversation %s: %s — "
            "falling back to heuristic",
            conversation_id, extraction_exc,
        )
        claims, method = [], "error"

    # Heuristic fallback when LLM extraction timed out or crashed.
    # extract_claims() has its own internal LLM→heuristic fallback, but if
    # the entire call timed out (e.g. Bifrost hung), we never reached the
    # heuristic path inside it.  Run it directly here.
    if not claims and method in ("timeout", "error"):
        try:
            from agents.hallucination.extraction import _extract_claims_heuristic
            claims = _extract_claims_heuristic(response_text)
            if claims:
                method = "heuristic"
                logger.info(
                    "Heuristic fallback produced %d claims after %s",
                    len(claims), method,
                )
        except Exception as heuristic_exc:
            logger.warning("Heuristic extraction also failed: %s", heuristic_exc)

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

    # Build topic context for claim verification (prevents ambiguous claims).
    # Always use heuristic in streaming to save 1-2s off the critical path.
    # The LLM context extraction adds latency without meaningful quality gain
    # in the streaming path where speed is prioritized.
    response_context = _heuristic_response_context(response_text, user_query)

    # Classify each claim's type for frontend display
    def _claim_type(claim_text: str) -> str:
        if claim_text.startswith("[EVASION]"):
            return "evasion"
        if claim_text.startswith("[CITATION]"):
            return "citation"
        if _is_ignorance_admission(claim_text):
            return "ignorance"
        if _is_recency_claim(claim_text):
            return "recency"
        # Apply temporal reclassification for date-based claims
        return _reclassify_recency(claim_text, "factual")

    # Notify frontend of extraction method and all extracted claims
    yield {"type": "extraction_complete", "method": method, "count": len(claims)}

    for i, claim in enumerate(claims):
        yield {
            "type": "claim_extracted",
            "claim": claim,
            "index": i,
            "claim_type": _claim_type(claim),
        }

    # --- Pre-fetch KB context for all claims in one batch ---
    # Reduces per-claim KB query overhead by sharing a single warm retrieval.
    batch_kb_context: list[dict[str, Any]] = []
    try:
        from agents.query_agent import lightweight_kb_query
        batch_query = " ".join(c for c in claims[:10])
        batch_kb_context = await lightweight_kb_query(
            batch_query, chroma_client=chroma_client, top_k=15,
        )
    except Exception as kb_exc:
        logger.debug("Batch KB pre-fetch failed (non-blocking): %s", kb_exc)

    # Build a set of claim indices where KB confidence is very high (>0.85),
    # allowing us to skip expensive external verification for those claims.
    high_confidence_kb_claims: set[int] = set()
    if batch_kb_context:
        for idx, claim_text in enumerate(claims):
            claim_lower = claim_text.lower()
            for kb_result in batch_kb_context:
                relevance = kb_result.get("relevance", 0.0)
                content = (kb_result.get("content", "") or "").lower()
                if relevance > 0.85 and claim_lower[:60] in content:
                    high_confidence_kb_claims.add(idx)
                    break

    # --- Batch pre-verification for current-event claims ---
    # Group time-sensitive claims (prices, recency) going to the same web-search
    # model and verify them in a single LLM call instead of N individual calls.
    # This reduces API round-trips, avoids rate limits, and prevents timeouts.
    from agents.hallucination.patterns import _is_current_event_claim
    from agents.hallucination.verification import verify_claims_batch_external

    batch_results: dict[int, dict[str, Any]] = {}
    current_event_claims: list[tuple[int, str]] = []
    for idx, claim_text in enumerate(claims):
        ct = _claim_type(claim_text)
        if ct in ("recency",) or _is_current_event_claim(claim_text):
            # Check cache first — don't re-batch already-cached claims
            from utils.claim_cache import get_cached_verdict
            cached = await get_cached_verdict(redis_client, claim_text)
            if cached and cached.get("status") in ("verified", "unverified"):
                batch_results[idx] = cached
            else:
                current_event_claims.append((idx, claim_text))

    # Track which indices are batch candidates — they'll be resolved by
    # the batch task running concurrently with individual verification.
    batch_candidate_indices: set[int] = {idx for idx, _ in current_event_claims}
    batch_task: asyncio.Task | None = None

    if current_event_claims and len(current_event_claims) >= 2:
        batch_model = config.VERIFICATION_CURRENT_EVENT_MODEL
        if expert_mode:
            batch_model = config.VERIFICATION_EXPERT_MODEL + ":online"

        async def _run_batch() -> None:
            """Run batch verification concurrently with individual claims."""
            try:
                batch_timeout = config.STREAMING_EXPERT_CLAIM_TIMEOUT
                batch_verdicts = await asyncio.wait_for(
                    verify_claims_batch_external(
                        current_event_claims,
                        model=batch_model,
                        response_context=response_context,
                        timeout=batch_timeout,
                    ),
                    timeout=batch_timeout + 5,
                )
                batch_results.update(batch_verdicts)
                # Pre-fill collected_results so individual tasks can skip
                for bidx, bresult in batch_verdicts.items():
                    collected_results[bidx] = bresult
                logger.info(
                    "Batch verified %d/%d current-event claims via %s",
                    len(batch_verdicts), len(current_event_claims), batch_model,
                )
            except (TimeoutError, Exception) as exc:
                logger.warning("Batch verification failed (%s), falling back to individual", exc)

        batch_task = asyncio.create_task(_run_batch())

    # --- Parallel verification via asyncio.as_completed ---
    verified_count = 0
    unverified_count = 0
    uncertain_count = 0
    skipped_count = 0
    assessed_confidence = 0.0  # Only accumulate for verified/unverified
    assessed_count = 0
    # NOTE: collected_results is shared with the concurrent batch_task
    collected_results: list[dict[str, Any] | None] = [None] * len(claims)
    stream_interrupted = False
    credit_exhausted = False
    credit_error_emitted = False

    # Pre-fill collected_results with cached batch results (non-async, immediate)
    for idx, result in batch_results.items():
        collected_results[idx] = result

    async def _verify_indexed(idx: int, claim_text: str) -> tuple[int, dict[str, Any]]:
        """Verify a single claim with a per-claim timeout and concurrency limit."""
        # For batch candidates, wait briefly for the concurrent batch task
        if idx in batch_candidate_indices and batch_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(batch_task), timeout=3.0)
            except (TimeoutError, Exception):
                pass  # batch not done yet or failed — proceed individually
        # Skip if already resolved by batch verification or cache
        if collected_results[idx] is not None:
            return idx, collected_results[idx]  # type: ignore[return-value]

        await _wait_for_memory(config.VERIFY_MEMORY_FLOOR_MB, f"claim-{idx}")
        sem = _get_claim_verify_semaphore()
        # Use extended timeout for expert mode (Grok 4 + :online web search)
        # and current-event claims that require web search + reasoning
        claim_timeout = (
            config.STREAMING_EXPERT_CLAIM_TIMEOUT
            if expert_mode or _claim_type(claim_text) == "recency"
            else config.STREAMING_PER_CLAIM_TIMEOUT
        )
        try:
            async with sem:
                result = await asyncio.wait_for(
                    verify_claim(
                        claim_text, chroma_client, neo4j_driver, redis_client,
                        threshold, model=model, streaming=True,
                        expert_mode=expert_mode,
                        source_artifact_ids=source_artifact_ids,
                        response_context=response_context,
                        pre_fetched_kb=batch_kb_context or None,
                    ),
                    timeout=claim_timeout,
                )
        except TimeoutError:
            logger.warning(
                "Claim %d verification timed out after %ds: '%s...'",
                idx, claim_timeout, claim_text[:50],
            )
            result = {
                "claim": claim_text,
                "status": "uncertain",
                "similarity": 0.0,
                "reason": f"Verification timed out ({int(claim_timeout)}s)",
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
            elif status == "skipped":
                skipped_count += 1
                # Track credit exhaustion for one-time event emission
                if result.get("credit_exhausted"):
                    credit_exhausted = True
            else:
                # Uncertain/unassessable claims excluded from confidence avg
                uncertain_count += 1

            collected_results[i] = result

            claim_event: dict[str, Any] = {
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
                **({"circular_source": True} if result.get("circular_source") else {}),
            }

            # Optional metamorphic scoring enrichment (Pro tier)
            try:
                from config.features import is_feature_enabled
                if is_feature_enabled("metamorphic_verification"):
                    from agents.hallucination.metamorphic import metamorphic_score
                    meta_result = await metamorphic_score(claims[i], response_context or "")
                    if meta_result and not meta_result.get("skipped"):
                        claim_event["metamorphic_score"] = meta_result
            except Exception as exc:
                logger.debug("Metamorphic scoring skipped: %s", exc)

            yield claim_event

            # Emit credit_error event once when first 402 is detected
            if result.get("credit_exhausted") and not credit_error_emitted:
                credit_error_emitted = True
                yield {
                    "type": "credit_error",
                    "message": "OpenRouter credits exhausted. Add credits at https://openrouter.ai/settings/credits",
                    "provider": "openrouter",
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
        "skipped": skipped_count,
        "total": len(claims),
        "assessed": assessed_count,
        "extraction_method": method,
        **({"interrupted": True} if stream_interrupted else {}),
        **({"credit_exhausted": True} if credit_exhausted else {}),
    }

    # --- Persist to Redis (same format as batch path) ---
    status_counts = {
        "verified": verified_count,
        "unverified": unverified_count,
        "uncertain": uncertain_count,
        "skipped": skipped_count,
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
