# Copyright (c) 2026 Cerid AI. All rights reserved.
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
from collections.abc import Sequence
from typing import Any

import config
from core.agents.hallucination.extraction import (
    _detect_evasion,
    _evasion_supersedes_primary,
    _extract_citation_claims,
    _extract_claims_heuristic,
    _extract_claims_llm,
    _extract_ignorance_claims,
    _merge_special_claims,
    _reclassify_recency,
    _resolve_pronouns_heuristic,
    extract_claims,
)
from core.agents.hallucination.patterns import (
    _get_claim_verify_semaphore,
    _has_staleness_indicators,
    _is_current_event_claim,
    _is_ignorance_admission,
    _is_recency_claim,
)
from core.agents.hallucination.persistence import (
    REDIS_HALLUCINATION_PREFIX,
    REDIS_HALLUCINATION_TTL,
    get_hallucination_report,
)
from core.agents.hallucination.verification import (
    _check_history_consistency,
    verify_claim,
)
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.hallucination")

_CGROUP_MEMORY_MAX = pathlib.Path("/sys/fs/cgroup/memory.max")
_CGROUP_MEMORY_CURRENT = pathlib.Path("/sys/fs/cgroup/memory.current")
_CGROUP_MEMORY_STAT = pathlib.Path("/sys/fs/cgroup/memory.stat")

# Fields in cgroup v2 memory.stat that represent memory the kernel can reclaim
# on demand without OOM-killing the container. Adding these to raw headroom
# gives the true "allocatable without OOM" budget — raw headroom alone is
# wrong because page cache and reclaimable slab look "used" but aren't pinned.
_RECLAIMABLE_STAT_KEYS = frozenset({"file", "slab_reclaimable"})

# Maximum time _wait_for_memory will block before giving up. The claim-verify
# semaphore + per-claim asyncio timeout are the real pressure regulators;
# this guard is observability + gentle backpressure, never a hard gate.
_MEMORY_WAIT_MAX_SECONDS = 5.0

# Task 12 / audit V-3: extract URLs the LLM cited inline in a claim so the
# verifier can check them *first* instead of re-searching from claim text.
# Matches bare http(s)://... URLs; stops at whitespace or common trailing
# punctuation. Conservative on purpose — the cost of a false positive is a
# single extra HEAD-like fetch; the cost of a miss is letting a fabricated
# citation through.
_URL_IN_CLAIM_RE = re.compile(r"https?://[^\s<>\"')\]}]+")


def _per_claim_base_timeout(expert_mode: bool, needs_web: bool) -> float:
    """Per-claim verification timeout (seconds) by strategy. Cross-model verifies
    run on OpenRouter via call_llm_raw; the cross-model + web caps are env-tunable
    because cloud latency under the verify semaphore can exceed a tight bound,
    causing timeouts + regeneration (CH5). The global STREAMING_TOTAL_TIMEOUT and
    the remaining-budget clamp at the call site still bound total runtime."""
    if expert_mode:
        return config.STREAMING_EXPERT_CLAIM_TIMEOUT
    if needs_web:
        return getattr(config, "STREAMING_WEB_CLAIM_TIMEOUT", 25.0)
    return getattr(config, "STREAMING_CROSS_MODEL_CLAIM_TIMEOUT", 18.0)


def _extract_source_urls_from_claim(claim_text: str) -> list[str]:
    """Pull any http(s) URLs the LLM cited inline in the claim text.

    These become ``source_urls`` for ``verify_claim`` which NLI-entails the
    claim against the cited body before falling back to KB / web search.
    De-duplicates preserving first-seen order; trims trailing punctuation.
    """
    if not claim_text or "http" not in claim_text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for match in _URL_IN_CLAIM_RE.finditer(claim_text):
        url = match.group(0).rstrip(".,;:!?")
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


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
        from core.utils.internal_llm import call_internal_llm

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
                stage="hallucination_topic",
            ),
            timeout=5.0,
        )
        topic = result.strip().strip('"').strip("'").strip(".")
        if topic and 3 < len(topic) < 150:
            logger.debug("LLM topic extraction: '%s'", topic)
            return topic
    except Exception as exc:
        # Heuristic fallback still fires; LLM failures here are not load-bearing.
        log_swallowed_error("core.agents.hallucination.streaming.extract_topic_llm", exc)

    return heuristic


def _read_reclaimable_bytes() -> int:
    """Sum cgroup v2 memory.stat fields that represent reclaimable memory.

    Returns 0 when memory.stat is unavailable or unparseable — the caller
    treats that as "no reclaimable memory detected" which is a safe under-
    estimate (biases toward pausing rather than overcommit).
    """
    try:
        stat = _CGROUP_MEMORY_STAT.read_text()
    except OSError:
        return 0
    total = 0
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key in _RECLAIMABLE_STAT_KEYS:
            try:
                total += int(value)
            except ValueError:
                continue
    return total


def _container_memory_available_mb() -> float | None:
    """Return memory allocatable without OOM, in MB, or None outside a cgroup.

    "Available" = raw headroom (``memory.max - memory.current``) plus
    reclaimable memory reported in ``memory.stat`` (file cache, reclaimable
    slab). The raw-headroom-only formula is wrong for long-running Python
    services: page cache and slab look "used" in ``memory.current`` but the
    kernel can evict them on demand without OOM-killing the container.
    """
    try:
        max_bytes = _CGROUP_MEMORY_MAX.read_text().strip()
        if max_bytes == "max":
            return None  # no limit set
        current_bytes = int(_CGROUP_MEMORY_CURRENT.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None  # not running in a cgroup-limited container
    headroom = int(max_bytes) - current_bytes
    return (headroom + _read_reclaimable_bytes()) / (1024 * 1024)


async def _wait_for_memory(floor_mb: int, label: str) -> None:
    """Briefly wait for container memory to clear ``floor_mb``; proceed regardless.

    Fail-open by design: after ``_MEMORY_WAIT_MAX_SECONDS`` the verifier runs
    even if the floor is still unmet. Verification is a lightweight I/O-bound
    workload (HTTP + small ONNX call, <10 MB per claim) so the semaphore and
    per-claim timeout are the real pressure regulators. An unbounded wait here
    deadlocks the verifier forever when steady-state memory legitimately sits
    near the cgroup cap.
    """
    deadline = time.monotonic() + _MEMORY_WAIT_MAX_SECONDS
    while time.monotonic() < deadline:
        available = _container_memory_available_mb()
        if available is None or available >= floor_mb:
            return
        logger.warning(
            "Verification paused (%s): container memory %.0fMB < %dMB floor",
            label, available, floor_mb,
        )
        await asyncio.sleep(1.0)
    logger.warning(
        "Verification memory floor (%dMB) not cleared within %.1fs — proceeding anyway (%s)",
        floor_mb, _MEMORY_WAIT_MAX_SECONDS, label,
    )


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
    create_memory_fn: Any = None,
    *,
    persist_report: bool = True,
) -> dict[str, Any]:
    """Extract claims, verify each against KB, and store results in Redis.

    ``persist_report`` (keyword-only) gates the durable ``hall:{cid}`` Redis
    write. Callers pass ``False`` when Private Mode blocks server-side saves
    (see ``app.services.private_mode.saves_blocked``); the app layer owns that
    decision because ``core`` cannot read private mode. Defaults ``True`` so
    every existing caller is unchanged.
    """
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

    # Expert mode pays for a premium verifier (Grok 4) + authoritative
    # external sources — give it a materially wider context window so the
    # investment pays off. Standard mode stays at ±200 to keep prompt cost
    # sane for gpt-4o-mini.
    _ctx_radius = 600 if expert_mode else 200

    def _extract_surrounding(claim_text: str) -> str | None:
        """Surrounding ±N chars from response_text — parallels the streaming path
        (`_extract_claim_context`) so cross-model verifiers see the same framing
        in both SSE and non-streaming flows. Radius grows in expert mode."""
        claim_start = response_text.find(claim_text[:40])
        if claim_start < 0:
            return None
        ctx_start = max(0, claim_start - _ctx_radius)
        ctx_end = min(len(response_text), claim_start + len(claim_text) + _ctx_radius)
        surrounding = response_text[ctx_start:ctx_end].strip()
        return surrounding if len(surrounding) > len(claim_text) + 20 else None

    # --- Batch pre-verification for current-event claims ---
    # Mirrors the streaming path's batch-optimization (see `streaming.py:485`):
    # group time-sensitive claims going to the same web-search model and verify
    # them in a single LLM call rather than N parallel calls. Reduces cost,
    # latency, and rate-limit pressure against the same resource.
    from core.agents.hallucination.verification import verify_claims_batch_external
    from core.utils.claim_cache import get_cached_verdict

    # Cache key (model, tier, response_context) must match what verify_claim
    # writes in verification.py — otherwise this batch pre-check always misses.
    # `verify_claim` below is invoked with `response_context=user_query`, so we
    # use the same value here.
    _cache_tier = "expert" if expert_mode else "standard"
    _cache_context = user_query or ""
    batch_results: dict[int, dict[str, Any]] = {}
    current_event_claims: list[tuple[int, str]] = []
    for idx, claim_text in enumerate(claims):
        if _is_current_event_claim(claim_text) or _is_recency_claim(claim_text):
            cached = await get_cached_verdict(
                redis_client,
                claim_text,
                model=model or "",
                method=_cache_tier,
                response_context=_cache_context,
            )
            if cached and cached.get("status") in ("verified", "unverified"):
                batch_results[idx] = cached
            else:
                current_event_claims.append((idx, claim_text))

    if len(current_event_claims) >= 2:
        batch_model = config.VERIFICATION_CURRENT_EVENT_MODEL
        if expert_mode:
            batch_model = config.VERIFICATION_EXPERT_WEB_MODEL
        try:
            batch_verdicts = await asyncio.wait_for(
                verify_claims_batch_external(
                    current_event_claims,
                    model=batch_model,
                    response_context=user_query,
                    timeout=config.STREAMING_EXPERT_CLAIM_TIMEOUT,
                ),
                timeout=config.STREAMING_EXPERT_CLAIM_TIMEOUT + 5,
            )
            batch_results.update(batch_verdicts)
            logger.info(
                "Non-streaming batch verified %d/%d current-event claims via %s",
                len(batch_verdicts), len(current_event_claims), batch_model,
            )
        except (TimeoutError, Exception) as exc:
            log_swallowed_error('core.agents.hallucination.streaming', exc)
            logger.warning(
                "Non-streaming batch verification failed (%s) — falling back to individual",
                exc,
            )

    async def _limited_verify(idx: int, claim_text: str) -> dict[str, Any]:
        # Skip individual verification if batch already resolved this claim.
        if idx in batch_results:
            return batch_results[idx]
        async with sem:
            return await verify_claim(
                claim_text, chroma_client, neo4j_driver, redis_client,
                threshold, model=model, expert_mode=expert_mode,
                # Context propagation — the streaming path threads these through;
                # the non-streaming path must too, or claims get validated in
                # isolation which produces false unverified verdicts on facts that
                # only make sense with their surrounding response (e.g. pronoun
                # references like "It is 8848.86 meters tall") or with the
                # topical frame the user asked about.
                response_context=user_query,
                claim_context=_extract_surrounding(claim_text),
                source_urls=_extract_source_urls_from_claim(claim_text),
            )

    results = await asyncio.gather(*[_limited_verify(i, c) for i, c in enumerate(claims)])

    status_counts = {"verified": 0, "unverified": 0, "uncertain": 0, "error": 0}
    assessed_confidence = 0.0
    assessed_count = 0
    for r in results:
        status = r.get("status", "error")
        if status in status_counts:
            status_counts[status] += 1
        # Mirror the streaming-path aggregate at streaming.py:984-989 — only
        # verified/unverified contribute to overall confidence; uncertain and
        # error are excluded so the score reflects what the verifier was
        # actually able to assess.
        if status in ("verified", "unverified"):
            assessed_confidence += float(r.get("similarity", 0.0))
            assessed_count += 1
    overall_confidence = (
        round(assessed_confidence / assessed_count, 3) if assessed_count else 0.0
    )

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
            "assessed": assessed_count,
            "overall_confidence": overall_confidence,
            **status_counts,
        },
    }

    if persist_report:
        try:
            key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
            redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(report))
        except Exception as e:
            log_swallowed_error('core.agents.hallucination.streaming', e)
            logger.warning("Failed to store hallucination report in Redis: %s", e)

    # --- Promote verified facts to empirical memories (non-streaming path) ---
    verified_count = status_counts.get("verified", 0)
    if config.ENABLE_VERIFIED_MEMORY_PROMOTION and verified_count > 0 and create_memory_fn is not None:
        try:
            from core.agents.verified_memory import promote_verified_facts

            _task = asyncio.create_task(promote_verified_facts(
                report,
                chroma_client=chroma_client,
                neo4j_driver=neo4j_driver,
                redis_client=redis_client,
                create_memory_fn=create_memory_fn,
            ))

            def _on_promotion_done(t: asyncio.Task) -> None:
                # Runtime failure inside promote_verified_facts (LLM timeouts,
                # graph errors, etc.). Resolved as issue #50: surface to
                # /health.swallowed_errors_last_hour for capacity-planning
                # instead of WARNING-only. The swallow is intentional —
                # crashing the verification path on a memory-promotion failure
                # would lose the user-visible verification result.
                if t.cancelled() or not t.exception():
                    return
                log_swallowed_error(
                    "core.agents.hallucination.streaming.promote_verified_facts_runtime",
                    t.exception(),  # type: ignore[arg-type]
                    redis_client=redis_client,
                )

            _task.add_done_callback(_on_promotion_done)
        except Exception as exc:  # noqa: BLE001 — dispatch failure is non-blocking
            # Dispatch-time failures (ImportError on lazy import, TypeError on
            # kwarg signature drift, RuntimeError if the loop is closed).
            # Issue #50 resolution: keep broad-catch with observability —
            # propagating to the parent would lose the entire response, which
            # is worse than a silent retry on the next request. The accumulation
            # signal lives on /health.swallowed_errors_last_hour.
            log_swallowed_error(
                "core.agents.hallucination.streaming.promote_verified_facts_dispatch",
                exc,
                redis_client=redis_client,
            )

    # Log verification metrics for analytics
    try:
        from core.utils.cache import log_verification_metrics
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
    except Exception as exc:
        log_swallowed_error(
            "core.agents.hallucination.streaming.log_verification_metrics",
            exc,
            redis_client=redis_client,
        )

    return report


# ---------------------------------------------------------------------------
# Streaming orchestration
# ---------------------------------------------------------------------------

def _summarize_claims(
    claims: Sequence[dict[str, Any] | None],
) -> tuple[dict[str, int], float]:
    """Recompute (status counts, overall_score) from a claims list.

    Single source of truth for a report's summary: every present claim lands in
    exactly one bucket, so verified+unverified+uncertain+skipped == total (the
    CR-037/CR-107 invariant — the summary agrees with its own claims array). A
    ``status='error'`` claim the sweep could not resolve folds into uncertain
    (matching the main loop's pre-sweep semantics) rather than vanishing from
    every counter. ``overall`` = mean similarity over assessed verified/unverified
    claims, which includes deadline-fallback verified claims (CR-115).
    """
    verified = sum(1 for r in claims if r and r.get("status") == "verified")
    unverified = sum(1 for r in claims if r and r.get("status") == "unverified")
    skipped = sum(1 for r in claims if r and r.get("status") == "skipped")
    # Everything present that is not verified/unverified/skipped (uncertain,
    # error, timeout, any unknown status) counts as uncertain so the total holds.
    uncertain = sum(
        1 for r in claims
        if r and r.get("status") not in ("verified", "unverified", "skipped")
    )
    assessed = [
        float(r.get("similarity", 0.0)) for r in claims
        if r and r.get("status") in ("verified", "unverified")
    ]
    overall = round(sum(assessed) / len(assessed), 3) if assessed else 0.0
    counts = {
        "verified": verified,
        "unverified": unverified,
        "uncertain": uncertain,
        "skipped": skipped,
        "total": len(claims),
        # Count of claims that contributed to overall (verified/unverified).
        # Interrupted runs still report this so clients can distinguish
        # "0 assessed" from "summary omitted the field" (audit residual).
        "assessed": len(assessed),
    }
    return counts, overall


def _merge_retry_into_existing(
    redis_client,
    conversation_id: str,
    index: int,
    new_verdict: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, int], float] | None:
    """E1 CR-019: fold a single-claim retry verdict into the existing durable report.

    Returns ``(claims, counts, overall)`` for the existing ``hall:{cid}`` report
    with ``claims[index]`` replaced by ``new_verdict`` (recomputed summary), or
    ``None`` when there is no existing report / the index is out of range / the
    retry produced no verdict — in which case the caller must SKIP the durable
    persist rather than clobber the N-claim report with a 1-claim one.
    """
    if new_verdict is None:
        return None
    existing = get_hallucination_report(redis_client, conversation_id)
    if not existing:
        return None
    claims = existing.get("claims")
    if not isinstance(claims, list) or not (0 <= index < len(claims)):
        return None
    merged_claim = dict(new_verdict)
    # A re-verification refreshes the verdict, not the human's thumbs signal —
    # preserve any prior user_feedback on this claim.
    prior = claims[index]
    if isinstance(prior, dict) and "user_feedback" in prior and "user_feedback" not in merged_claim:
        merged_claim["user_feedback"] = prior["user_feedback"]
    merged = list(claims)
    merged[index] = merged_claim
    counts, overall = _summarize_claims(merged)
    return merged, counts, overall


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
    create_memory_fn: Any = None,
    save_report_fn: Any = None,
    *,
    persist_report: bool = True,
    merge_claim_index: int | None = None,
):
    """Streaming verification generator — yields claim results as they are verified.

    Results are yielded as they complete (parallel execution), then persisted
    to Redis after the final summary for audit analytics and conversation revisits.

    When ``expert_mode`` is True, all claims are verified using the expert-tier
    model (Grok 4) instead of the default model pool.

    When ``source_artifact_ids`` is provided, KB results matching those IDs are
    penalised during confidence scoring to prevent circular self-verification
    (the KB confirming claims that were originally derived from it).

    When ``save_report_fn`` is provided, it is invoked once after the retry
    sweep and consistency checks complete with the final counts + collected
    results. Layering: ``core/`` cannot import from ``app/`` (the Neo4j save
    helper lives in ``app.db.neo4j.artifacts``), so the router threads a
    bound closure here — mirrors the ``create_memory_fn`` DI pattern. A
    ``{"type": "persisted", "success": bool}`` event is yielded after the
    save attempt so the frontend can skip its own redundant save call.

    ``merge_claim_index`` marks a single-claim retry (E1 CR-019): the FE re-runs
    ONE claim from an N-claim report through this same endpoint (``response_text``
    is just that claim, under the original ``conversation_id``). Without it, the
    run's durable persist would REPLACE the N-claim ``hall:{cid}`` Redis + Neo4j
    report with a 1-claim report, destroying the other claims' verdicts and
    invalidating their feedback indices. When set, the fresh verdict is instead
    MERGED into ``claims[merge_claim_index]`` of the existing durable report
    (recomputing the summary), and if there is no existing report / the index is
    out of range the durable persist is SKIPPED rather than clobbering it.
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

    # ── Stage 1: Synchronous heuristic extraction (<5ms) ──────────────
    max_claims = config.HALLUCINATION_MAX_CLAIMS
    ignorance_claims = _extract_ignorance_claims(response_text)
    evasion_claims = _detect_evasion(response_text, user_query) if user_query else []
    citation_claims = _extract_citation_claims(response_text)
    if _evasion_supersedes_primary(response_text, evasion_claims):
        # Whole-response evasion: heuristic output would only restate the
        # hedge — see extraction._evasion_supersedes_primary. The nonempty
        # special set below also short-circuits Stage 2's LLM extraction.
        heuristic_claims: list[str] = []
    else:
        heuristic_raw = _extract_claims_heuristic(response_text)
        heuristic_claims = _resolve_pronouns_heuristic(heuristic_raw, response_text, user_query)
    initial_claims = _merge_special_claims(
        heuristic_claims, ignorance_claims, evasion_claims, citation_claims, max_claims,
    )

    # ── Stage 2: Decision ─────────────────────────────────────────────
    # If heuristic found claims → use them immediately (zero LLM wait).
    # If heuristic found nothing → fall back to LLM extraction (complex/conversational response).
    if initial_claims:
        method = "heuristic"
    else:
        # No heuristic claims — must wait for LLM
        try:
            llm_result = await asyncio.wait_for(
                _extract_claims_llm(response_text, max_claims, user_query=user_query),
                timeout=30,
            )
            initial_claims = _merge_special_claims(
                llm_result or [], ignorance_claims, evasion_claims, citation_claims, max_claims,
            )
            method = "llm" if llm_result else "none"
        except TimeoutError:
            logger.error("Claim extraction timed out for conversation %s", conversation_id)
            method = "timeout"
        except Exception as exc:
            log_swallowed_error('core.agents.hallucination.streaming', exc)
            logger.error("Claim extraction failed for %s: %s", conversation_id, exc)
            method = "error"

    claims = initial_claims
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
    if user_query:
        response_context = _heuristic_response_context(response_text, user_query)
    else:
        response_context = await _extract_response_context(response_text, user_query)

    # Enrich context with the full claim list — when verifying "red: 620-750nm",
    # the verifier needs to know the response listed ALL wavelengths in a table.
    # This prevents false refutations on decontextualized numeric claims.
    if claims and len(claims) > 1:
        claim_list_ctx = " | ".join(c[:80] for c in claims[:10])
        response_context = (
            f"{response_context or ''}\n"
            f"Other claims in the same response: {claim_list_ctx}"
        ).strip()

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

    # Response-level staleness: "as of my knowledge cutoff…" framing gets
    # stripped from individual claims at extraction time, so a stale-cutoff
    # admission must be detected on the full response and propagated — every
    # factual claim in such a response needs a live web check, not a KB echo.
    response_is_stale = _has_staleness_indicators(response_text)

    # --- Pre-fetch KB context for all claims in one batch ---
    # Reduces per-claim KB query overhead by sharing a single warm retrieval.
    batch_kb_context: list[dict[str, Any]] = []
    try:
        from core.agents.query_agent import lightweight_kb_query
        batch_query = " ".join(c for c in claims[:10])
        # Anti-circularity: mirror the per-claim path — never retrieve from
        # the conversations domain, or a response verifies against its own
        # (or a prior turn's) transcript once it gets ingested.
        batch_domains = [d for d in config.DOMAINS if d != "conversations"]
        batch_kb_context = await lightweight_kb_query(
            batch_query, domains=batch_domains, chroma_client=chroma_client,
            top_k=15,
        )
    except Exception as kb_exc:
        # Batch pre-fetch is an optimization; per-claim queries still run.
        log_swallowed_error(
            "core.agents.hallucination.streaming.batch_kb_prefetch",
            kb_exc,
            redis_client=redis_client,
        )

    # Track verification progress per claim for graceful timeout handling.
    # When the total deadline fires, claims that completed KB evidence gathering
    # (Phase 2) but were waiting for external verdict (Phase 3) can use their
    # KB-only result as a fallback instead of blanket "uncertain".
    _claim_evidence: dict[int, dict] = {}  # {claim_index: {"kb_results": [...], "kb_quality": float}}

    # Populate _claim_evidence from batch KB pre-fetch so that timeout
    # fallback can use KB-only verdicts for claims that gathered evidence.
    if batch_kb_context:
        for idx, claim_text in enumerate(claims):
            claim_lower = claim_text.lower()
            matching_results = [
                r for r in batch_kb_context
                if claim_lower[:40] in (r.get("content", "") or "").lower()
            ]
            if matching_results:
                best_quality = max(
                    (r.get("relevance", 0.0) for r in matching_results), default=0.0,
                )
                _claim_evidence[idx] = {
                    "kb_results": matching_results[:5],
                    "kb_quality": best_quality,
                }

    # Map claim indices to their best-matching KB result when confidence is
    # very high (>0.85 relevance AND content overlap).  Pre-resolving these
    # as "verified" avoids the full verify_claim pipeline (KB re-query,
    # confidence calibration, external fallback) — a significant speedup
    # when the batch pre-fetch already found strong evidence.
    high_confidence_kb_claims: dict[int, dict[str, Any]] = {}
    if batch_kb_context:
        excluded_artifact_ids = set(source_artifact_ids or [])
        for idx, claim_text in enumerate(claims):
            # Time-sensitive claim types must run the full verify_claim
            # pipeline (staleness gates + forced web checks) — a KB snapshot
            # match can't confirm them. A response that admits a stale
            # knowledge cutoff taints every claim it made the same way.
            if response_is_stale or _claim_type(claim_text) != "factual":
                continue
            claim_lower = claim_text.lower()
            for kb_result in batch_kb_context:
                # Anti-circularity (defense in depth alongside the domain
                # exclusion above): never pre-resolve against the artifacts
                # the response itself was generated from, prior transcripts,
                # or episodic memories.
                if (
                    kb_result.get("artifact_id") in excluded_artifact_ids
                    or kb_result.get("domain") == "conversations"
                    or kb_result.get("memory_source")
                ):
                    continue
                relevance = kb_result.get("relevance", 0.0)
                raw_content = kb_result.get("content", "") or ""
                content = raw_content.lower()
                if relevance > 0.85 and claim_lower[:60] in content:
                    # Relevance + substring is exactly the "shared keywords,
                    # different topic" false positive the slow path's semantic-
                    # alignment gate refuses. Require NLI entailment before
                    # stamping verified so the fast path can't over-claim on
                    # lexical overlap alone. NLI runs ONLY here, on the few
                    # candidates that already passed the cheap checks.
                    try:
                        from core.utils.nli import nli_score_async
                        entailment = (
                            await nli_score_async(raw_content[:512], claim_text)
                        )["entailment"]
                    except Exception as nli_exc:
                        log_swallowed_error(
                            "core.agents.hallucination.streaming.kb_batch_nli",
                            nli_exc,
                            redis_client=redis_client,
                        )
                        # NLI unavailable — do NOT pre-resolve; the full
                        # verify_claim pipeline handles this claim.
                        break
                    if entailment < config.NLI_ENTAILMENT_THRESHOLD:
                        # Lexical match without entailment — try the next
                        # candidate, else fall through to full verification.
                        continue
                    high_confidence_kb_claims[idx] = {
                        "claim": claim_text,
                        "status": "verified",
                        "similarity": round(relevance, 3),
                        "source_artifact_id": kb_result.get("artifact_id", ""),
                        "source_filename": kb_result.get("filename", ""),
                        "source_domain": kb_result.get("domain", ""),
                        "source_snippet": raw_content[:200],
                        "verification_method": "kb_batch",
                        "memory_source": bool(kb_result.get("memory_source")),
                    }
                    break

    # --- Batch pre-verification for current-event claims ---
    # Group time-sensitive claims (prices, recency) going to the same web-search
    # model and verify them in a single LLM call instead of N individual calls.
    # This reduces API round-trips, avoids rate limits, and prevents timeouts.
    from core.agents.hallucination.verification import verify_claims_batch_external

    # Cache key (model, tier, response_context) must match what verify_claim
    # writes in verification.py — otherwise this pre-check always misses.
    _cache_tier = "expert" if expert_mode else "standard"
    batch_results: dict[int, dict[str, Any]] = {}
    current_event_claims: list[tuple[int, str]] = []
    for idx, claim_text in enumerate(claims):
        ct = _claim_type(claim_text)
        # Stale-cutoff responses route their factual claims through the same
        # batched web check as recency claims — one :online call for all of
        # them instead of N individual calls that blow the per-claim budget.
        # (ignorance/evasion/citation claims stay on the individual path:
        # their verdicts need direction-specific inversion the batch prompt
        # doesn't perform.)
        if (
            ct == "recency"
            or _is_current_event_claim(claim_text)
            or (response_is_stale and ct == "factual")
        ):
            # Check cache first — don't re-batch already-cached claims
            from core.utils.claim_cache import get_cached_verdict
            cached = await get_cached_verdict(
                redis_client,
                claim_text,
                model=model or "",
                method=_cache_tier,
                response_context=response_context or "",
            )
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
            batch_model = config.VERIFICATION_EXPERT_WEB_MODEL

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
                log_swallowed_error('core.agents.hallucination.streaming', exc)
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

    # Pre-fill high-confidence KB claims — these skip the full verify_claim
    # pipeline entirely because the batch pre-fetch already found strong
    # evidence (>0.85 relevance + content overlap).
    for idx, result in high_confidence_kb_claims.items():
        if collected_results[idx] is None:  # Don't overwrite batch verdicts
            collected_results[idx] = result
    if high_confidence_kb_claims:
        logger.info(
            "Pre-resolved %d/%d claims via high-confidence KB batch",
            len(high_confidence_kb_claims), len(claims),
        )

    def _extract_claim_context(claim_text: str) -> str | None:
        """Extract 1-2 sentences before and after the claim in the original response."""
        # Find the claim in the response text
        claim_start = response_text.find(claim_text[:40])
        if claim_start < 0:
            return None
        # Get ~200 chars before and after for surrounding context
        ctx_start = max(0, claim_start - 200)
        ctx_end = min(len(response_text), claim_start + len(claim_text) + 200)
        surrounding = response_text[ctx_start:ctx_end].strip()
        return surrounding if len(surrounding) > len(claim_text) + 20 else None

    async def _verify_indexed(idx: int, claim_text: str) -> tuple[int, dict[str, Any]]:
        """Verify a single claim with a per-claim timeout and concurrency limit."""
        # For batch candidates, wait briefly for the concurrent batch task
        if idx in batch_candidate_indices and batch_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(batch_task), timeout=3.0)
            except (TimeoutError, Exception):  # silent-catch-allowed: batch race; fall back to per-claim verification
                pass  # batch not done yet or failed — proceed individually
        # Skip if already resolved by batch verification or cache
        if collected_results[idx] is not None:
            return idx, collected_results[idx]  # type: ignore[return-value]

        await _wait_for_memory(config.VERIFY_MEMORY_FLOOR_MB, f"claim-{idx}")
        sem = _get_claim_verify_semaphore()
        # Adaptive timeout based on verification strategy (config defaults):
        #   - Expert mode (web-search reasoning model): longest (30s)
        #   - Web search claims (temporal, current-event, citation): medium (25s)
        #   - Cross-model (general factual): fastest (18s)
        ct = _claim_type(claim_text)
        needs_web = (
            ct in ("recency", "evasion", "citation", "ignorance")
            or _is_current_event_claim(claim_text)
            or response_is_stale
        )
        per_claim_timeout = _per_claim_base_timeout(expert_mode, needs_web)
        # Cap per-claim timeout to remaining global budget (leave 2s buffer for summary)
        remaining = stream_deadline - time.monotonic()
        claim_timeout = min(per_claim_timeout, max(remaining - 2.0, 3.0))
        try:
            async with sem:
                # Deadline is measured from NOW (post-semaphore) — queueing
                # behind other claims must not consume this claim's budget.
                result = await asyncio.wait_for(
                    verify_claim(
                        claim_text, chroma_client, neo4j_driver, redis_client,
                        threshold, model=model, streaming=True,
                        expert_mode=expert_mode,
                        source_artifact_ids=source_artifact_ids,
                        response_context=response_context,
                        claim_context=_extract_claim_context(claim_text),
                        conversation_context=conversation_history,
                        source_urls=_extract_source_urls_from_claim(claim_text),
                        deadline=time.monotonic() + claim_timeout,
                        stale_context=response_is_stale,
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

    def _claim_verified_event(i: int, result: dict[str, Any]) -> dict[str, Any]:
        """Build the claim_verified SSE event for a settled claim result.

        Shared by the main verification loop and the round-2 retry sweep so
        sweep-resolved verdicts reach the frontend in the exact same shape
        (pre-2026-07-13 the sweep only updated the persisted report and the
        UI kept showing the stale timeout verdicts).
        """
        return {
            "type": "claim_verified",
            "index": i,
            "claim": claims[i],
            "claim_type": _claim_type(claims[i]),
            "status": result.get("status", "error"),
            "confidence": result.get("similarity", 0.0),
            "source": result.get("source_filename", ""),
            "source_artifact_id": result.get("source_artifact_id", ""),
            "source_domain": result.get("source_domain", ""),
            "source_snippet": result.get("source_snippet", ""),
            "reason": result.get("reason", ""),
            "verification_method": result.get("verification_method", "kb"),
            "verification_model": result.get("verification_model"),
            # E1 CR-042: NLI entailment/contradiction + memory_source are computed
            # by verify_claim and kept in the persisted report, but were dropped
            # here — so the FE provenance popover's NLI verdict was dark on every
            # streamed claim. Emit them so the wire shape matches the stored truth.
            "nli_entailment": result.get("nli_entailment"),
            "nli_contradiction": result.get("nli_contradiction"),
            "memory_source": result.get("memory_source"),
            "source_urls": result.get("source_urls", []),
            "verification_answer": result.get("verification_answer", ""),
            # Expert-mode authoritative evidence — surfaces per-source NLI
            # scores, domain classification, and KB-vs-external cross
            # validation so the UI can show *why* a verdict was reached.
            # Fields are omitted entirely when not in expert/authoritative path.
            **(
                {"authoritative_sources": result["authoritative_sources"]}
                if result.get("authoritative_sources") else {}
            ),
            **(
                {"claim_domain": result["claim_domain"]}
                if result.get("claim_domain") else {}
            ),
            **(
                {"cross_validation": result["cross_validation"]}
                if result.get("cross_validation") else {}
            ),
            **(
                {"evidence_summary": result["evidence_summary"]}
                if result.get("evidence_summary") else {}
            ),
            **({"circular_source": True} if result.get("circular_source") else {}),
        }

    def _fallback_result(j: int) -> dict[str, Any]:
        """Canonical settled result for a claim the total deadline left unverified.

        E1 CR-036: same shape as a ``verify_claim`` result — top-level status /
        similarity / verification_method / source_filename — so it flows through
        ``_claim_verified_event`` and the persisted report identically, instead of
        the old divergent event that nested the verdict and leaked the sentinel
        ``kb_only_timeout`` / ``timeout`` label into the ``source`` (source_filename)
        field. A KB-only verdict is used when Phase-2 evidence exists, else a plain
        timeout verdict.
        """
        evidence = _claim_evidence.get(j)
        # E1 R15: always include ``claim`` so interrupted-run report entries
        # retain the claim text for UI + feedback indices.
        claim_text = claims[j] if 0 <= j < len(claims) else ""
        if evidence and evidence["kb_quality"] >= 0.35:
            kb_status = "verified" if evidence["kb_quality"] >= 0.65 else "uncertain"
            sources = [
                r.get("source", "") for r in evidence["kb_results"][:3] if r.get("source")
            ]
            return {
                "claim": claim_text,
                "status": kb_status,
                "similarity": evidence["kb_quality"],
                "confidence": evidence["kb_quality"],
                "reason": "KB-only verdict (verification timeout)",
                "verification_method": "kb_only_timeout",
                "source_filename": sources[0] if sources else "",
                "source_urls": [],
            }
        return {
            "claim": claim_text,
            "status": "uncertain",
            "similarity": 0.0,
            "confidence": 0.0,
            "reason": "Verification timeout — insufficient evidence",
            "verification_method": "timeout",
            "source_filename": "",
        }

    def _settle_timeouts():
        """Settle every claim the total deadline left unverified.

        E1 CR-037: writes the fallback result into ``collected_results`` (so the
        persisted claims array + summary counts + feedback indices agree with the
        UI, instead of counting claims the report never stored) and emits each
        through the shared ``_claim_verified_event`` builder.
        """
        nonlocal verified_count, uncertain_count
        for j in range(len(claims)):
            if collected_results[j] is not None:
                continue  # already has a verdict
            result = _fallback_result(j)
            collected_results[j] = result
            if result["status"] == "verified":
                verified_count += 1
            else:
                uncertain_count += 1
            yield _claim_verified_event(j, result)

    # Real Task objects (not bare coroutines) so the deadline break can cancel
    # the still-pending ones — asyncio.as_completed would otherwise wrap them in
    # internal tasks we hold no reference to (E1 CR-106).
    tasks = [asyncio.ensure_future(_verify_indexed(i, claim))
             for i, claim in enumerate(claims)]

    async def _drain_background() -> None:
        """Cancel-and-drain the still-pending background verification work.

        E1 CR-105/106. Called on the deadline break (BEFORE ``_settle_timeouts``,
        so a late ``batch_task`` completion can't race the fallback verdicts by
        overwriting ``collected_results``) and again after the loop (so a normal
        exit doesn't orphan a still-running batch task, and an exception-
        interrupted loop doesn't leak its in-flight per-claim tasks). Leaked
        per-claim tasks otherwise keep holding the *process-global* claim-verify
        semaphore + burning LLM spend after their verdicts can no longer be used;
        a leaked batch task mutates ``collected_results`` at a nondeterministic
        point vs. the report snapshot. Idempotent — done tasks are skipped, so
        the second call is a no-op on the happy path.
        """
        pending = [t for t in tasks if not t.done()]
        if batch_task is not None and not batch_task.done():
            pending.append(batch_task)
        if not pending:
            return
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # Total deadline prevents the verification loop from running forever.
    # Individual claims have per-claim timeouts, but the total deadline
    # catches edge cases where many claims each take close to the limit.
    stream_deadline = time.monotonic() + config.STREAMING_TOTAL_TIMEOUT

    # Wrap verification loop in try/except/finally to guarantee summary emission
    # AND background drain. Without finally, a client disconnect (GeneratorExit
    # into this async generator — not Exception) orphans batch/claim tasks
    # (E1 R8 / CR-105 disconnect leg).
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
                # E1 CR-105/106: cancel the in-flight verify tasks + batch task
                # before settling, so they stop holding the process-global
                # semaphore and can't race the fallback verdicts.
                await _drain_background()
                # E1 CR-036/037: settle timed-out claims through the shared builder
                # AND into collected_results (was a divergent, uncollected event).
                for _fallback_ev in _settle_timeouts():
                    yield _fallback_ev
                break
            try:
                i, result = await asyncio.wait_for(coro, timeout=remaining)
            except TimeoutError:
                logger.warning(
                    "Stream deadline expired waiting for claim result "
                    "(%ds total)", config.STREAMING_TOTAL_TIMEOUT,
                )
                stream_interrupted = True
                # E1 CR-105/106: cancel in-flight verify + batch tasks before
                # settling (identical to the deadline-check branch above).
                await _drain_background()
                # E1 CR-036/037: settle via the shared builder + collected_results
                # (identical to the deadline-check branch above).
                for _fallback_ev in _settle_timeouts():
                    yield _fallback_ev
                break
            except Exception as task_exc:
                logger.warning("Verification task failed: %s", task_exc)
                try:
                    from core.utils.cache import log_verification_error
                    log_verification_error(
                        redis_client, conversation_id,
                        error_type="claim_verification_failed",
                        error_message=str(task_exc),
                        model=model, phase="verification",
                    )
                except Exception as log_exc:
                    log_swallowed_error(
                        "core.agents.hallucination.streaming.log_claim_verification_failed",
                        log_exc,
                        redis_client=redis_client,
                    )
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

            yield _claim_verified_event(i, result)

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
            from core.utils.cache import log_verification_error
            log_verification_error(
                redis_client, conversation_id,
                error_type="stream_interrupted",
                error_message=str(loop_exc),
                model=model, phase="verification",
            )
        except Exception as log_exc:
            log_swallowed_error(
                "core.agents.hallucination.streaming.log_stream_interrupted",
                log_exc,
                redis_client=redis_client,
            )
        # Explicit SSE error event — without this the frontend sees the stream
        # end mid-verification with no diagnostic and has to guess whether it
        # succeeded, failed, or is hung. The summary at the bottom of this
        # generator is still emitted as a "guaranteed" terminator; this event
        # tells the client *why* claims after this point are missing.
        yield {
            "type": "error",
            "error_type": "stream_interrupted",
            "phase": "verification",
            "message": str(loop_exc)[:500],
            "claims_seen": verified_count + unverified_count + uncertain_count,
            "claims_total": len(claims),
            "recoverable": True,
        }
    finally:
        # E1 CR-105 / R8: always drain — covers happy path, deadline, Exception,
        # and GeneratorExit (client disconnect / SSE teardown). Idempotent.
        await _drain_background()

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
    # E1 CR-115: derive overall from the claim set's similarity rather than the
    # assessed_confidence/count accumulators — _settle_timeouts records a
    # deadline-fallback verified claim's similarity into collected_results but not
    # into those accumulators, so an interrupted run otherwise reports verified>0
    # with overall_confidence 0.
    _assessed_sims = [
        float((r or {}).get("similarity", 0.0))
        for r in collected_results
        if r and r.get("status") in ("verified", "unverified")
    ]
    overall = (sum(_assessed_sims) / len(_assessed_sims)) if _assessed_sims else 0
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

    # The report build + Redis persist + memory promotion moved BELOW the Round-2
    # sweep (E1 CR-044/045): building them here snapshotted collected_results
    # BEFORE the sweep resolved timeout/error claims, so the persisted hall:{cid}
    # report and the memory-promotion input were stale while the SSE stream + Neo4j
    # write got the corrected verdicts.

    # --- Round 2 sweep: retry timed-out and errored claims ---
    # Claims that timed out (sim=0.0, method=timeout) or errored were not
    # properly evaluated. Retry them with a lightweight path (no streaming,
    # tighter timeout) now that the main verification pressure is off.
    retry_indices = [
        i for i, r in enumerate(collected_results)
        if r and r.get("verification_method") in ("timeout", "none")
        or r and r.get("status") == "error"
    ]
    if retry_indices and not stream_interrupted:
        retry_budget = min(15.0, config.STREAMING_TOTAL_TIMEOUT * 0.15)
        retry_sem = asyncio.Semaphore(3)
        sweep_resolved: list[int] = []

        async def _retry_claim(idx: int) -> None:
            claim_text = claims[idx]
            try:
                async with retry_sem:
                    result = await asyncio.wait_for(
                        verify_claim(
                            claim_text, chroma_client, neo4j_driver, redis_client,
                            threshold, model=model, streaming=False,
                            expert_mode=False,
                            response_context=response_context,
                            claim_context=_extract_claim_context(claim_text),
                            source_urls=_extract_source_urls_from_claim(claim_text),
                            deadline=time.monotonic() + retry_budget,
                            stale_context=response_is_stale,
                        ),
                        timeout=retry_budget,
                    )
                    if result.get("status") in ("verified", "unverified"):
                        collected_results[idx] = result
                        sweep_resolved.append(idx)
                        old_status = "timeout/error"
                        logger.info(
                            "Sweep retry resolved claim %d: %s → %s ('%s...')",
                            idx, old_status, result["status"], claim_text[:40],
                        )
            except (TimeoutError, Exception):  # silent-catch-allowed: retry timed out/failed; keep original result
                pass  # Keep the original timeout/error result

        try:
            await asyncio.wait_for(
                asyncio.gather(*[_retry_claim(i) for i in retry_indices]),
                timeout=retry_budget + 2.0,
            )
        except TimeoutError:
            # Budget exhaustion is an expected fallback path, not an error.
            logger.info("Sweep retry budget exhausted")

        # Recount after sweep. E1 CR-107: fold status='error' into uncertain (a
        # claim the sweep could not resolve must not vanish from every counter),
        # matching _summarize_claims and the pre-sweep main-loop semantics.
        verified_count = sum(1 for r in collected_results if r and r.get("status") == "verified")
        unverified_count = sum(1 for r in collected_results if r and r.get("status") == "unverified")
        uncertain_count = sum(
            1 for r in collected_results
            if r and r.get("status") not in ("verified", "unverified", "skipped")
        )

        # Push the corrected verdicts to the frontend. The summary above was
        # emitted pre-sweep; without these events the UI keeps showing the
        # stale timeout verdicts while the persisted report has the resolved
        # ones (the two disagreed until 2026-07-13).
        for idx in sweep_resolved:
            resolved_result = collected_results[idx]
            if resolved_result is not None:
                yield _claim_verified_event(idx, resolved_result)
        if sweep_resolved:
            assessed = verified_count + unverified_count
            yield {
                "type": "summary_update",
                "verified": verified_count,
                "unverified": unverified_count,
                "uncertain": uncertain_count,
                "total": len(claims),
                "assessed": assessed,
                "overall_confidence": round(
                    sum(
                        (r or {}).get("similarity", 0.0)
                        for r in collected_results
                        if r and r.get("status") in ("verified", "unverified")
                    ) / assessed,
                    3,
                ) if assessed else 0.0,
            }

    # --- Build + persist the AUTHORITATIVE post-sweep report (E1 CR-044/045) ---
    # Built here — after the retry sweep resolved timeout/error claims and
    # recounted — so the Redis hall:{cid} snapshot and the memory-promotion input
    # both reflect the settled verdicts, matching the SSE stream + the Neo4j write.
    run_claims = [r for r in collected_results if r is not None]
    # E1 CR-104/107/115/067: derive the authoritative summary from the persisted
    # claims array itself (single source of truth) rather than the counters kept
    # by side-effect across the main loop, _settle_timeouts, and the recount. This
    # counts every present claim regardless of which actor wrote it (CR-104), folds
    # status='error' into the total (CR-107), and computes overall from claim
    # similarity so deadline-fallback verified claims contribute (CR-115) and the
    # score agrees with the counts rather than a stale pre-sweep one (CR-067).
    status_counts, run_overall = _summarize_claims(run_claims)
    report = {
        "conversation_id": conversation_id,
        "timestamp": utcnow_iso(),
        "skipped": False,
        "threshold": threshold,
        "model": model,
        "extraction_method": method,
        "claims": run_claims,
        "summary": status_counts,
    }

    # E1 CR-019: the durable stores (Redis hall:{cid} + the Neo4j save_report_fn)
    # get the DURABLE report, which diverges from the fresh run report only on a
    # single-claim retry: there the run verified just one claim, and replacing the
    # N-claim report with it would destroy the other claims' verdicts + feedback
    # indices. Merge the one verdict into the existing report instead; if there is
    # no existing report / bad index, skip the durable persist (never clobber).
    # `report` (the fresh run) is what promotion sees, so a retry only promotes its
    # own claim rather than re-promoting the whole merged report.
    durable_claims = run_claims
    durable_counts: dict[str, int] = dict(status_counts)
    durable_overall = round(run_overall, 3)
    skip_durable = False
    if merge_claim_index is not None:
        merged = _merge_retry_into_existing(
            redis_client, conversation_id, merge_claim_index,
            run_claims[0] if run_claims else None,
        )
        if merged is None:
            skip_durable = True
        else:
            durable_claims, durable_counts, durable_overall = merged

    durable_report = report if merge_claim_index is None else {
        **report,
        "claims": durable_claims,
        "summary": {
            "total": durable_counts["total"],
            "verified": durable_counts["verified"],
            "unverified": durable_counts["unverified"],
            "uncertain": durable_counts["uncertain"],
            "skipped": durable_counts["skipped"],
        },
    }
    # E1 R9: provisional hall:{cid} write NOW (post-sweep authoritative report)
    # so claim feedback submitted during the consistency await does not land on
    # a previous conversation's report. Final write after consistency fold-in
    # overwrites with consistency_issue annotations (CR-113).
    if persist_report and not skip_durable:
        try:
            key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
            redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(durable_report))
        except Exception as e:
            log_swallowed_error(
                "core.agents.hallucination.streaming.provisional_hall_persist", e,
            )
    # E1 CR-113: final Redis hall:{cid} write still runs AFTER the consistency
    # fold-in below so the durable copy carries consistency_issue annotations.

    # --- Promote verified facts to empirical memories (fire-and-forget) ---
    _create_mem_fn = create_memory_fn
    # E1 CR-116: skip promotion on interrupted / credit-exhausted runs, mirroring
    # the Neo4j auto-persist gate below — a partial run's verified_count includes
    # deadline-fallback verdicts, and promoting them creates :Memory nodes from a
    # report that was deliberately never durably saved.
    if (
        config.ENABLE_VERIFIED_MEMORY_PROMOTION
        and verified_count > 0
        and _create_mem_fn is not None
        and not stream_interrupted
        and not credit_exhausted
    ):
        try:
            from core.agents.verified_memory import promote_verified_facts

            _task = asyncio.create_task(promote_verified_facts(
                report,
                chroma_client=chroma_client,
                neo4j_driver=neo4j_driver,
                redis_client=redis_client,
                create_memory_fn=_create_mem_fn,
            ))

            def _on_promotion_done_streaming(t: asyncio.Task) -> None:
                # Streaming-path twin of _on_promotion_done above; resolved
                # as issue #50.
                if t.cancelled() or not t.exception():
                    return
                log_swallowed_error(
                    "core.agents.hallucination.streaming.promote_verified_facts_runtime_streaming",
                    t.exception(),  # type: ignore[arg-type]
                    redis_client=redis_client,
                )

            _task.add_done_callback(_on_promotion_done_streaming)
        except Exception as exc:  # noqa: BLE001 — dispatch failure is non-blocking
            # Streaming-path twin of the dispatch swallow above; resolved as
            # issue #50.
            log_swallowed_error(
                "core.agents.hallucination.streaming.promote_verified_facts_dispatch_streaming",
                exc,
                redis_client=redis_client,
            )

    try:
        from core.utils.cache import log_verification_metrics
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
    except Exception as exc:
        log_swallowed_error(
            "core.agents.hallucination.streaming.log_streaming_verification_metrics",
            exc,
            redis_client=redis_client,
        )

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
                from core.utils.cache import log_verification_error
                log_verification_error(
                    redis_client, conversation_id,
                    error_type="consistency_check_failed",
                    error_message=str(e),
                    model=model, phase="consistency",
                )
            except Exception as log_exc:
                log_swallowed_error(
                    "core.agents.hallucination.streaming.log_consistency_check_failed",
                    log_exc,
                    redis_client=redis_client,
                )

    # E1 CR-113: persist the durable Redis hall:{cid} report HERE — after the
    # consistency fold-in mutated collected_results (whose dicts durable_report's
    # claims share by reference on the common non-merge path) — so the Redis copy
    # carries consistency_issue like the Neo4j write below. Deferred from the
    # report-build site above, where it serialized before the fold-in and left the
    # two stores permanently disagreeing on consistency_issue.
    if persist_report and not skip_durable:
        try:
            key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
            redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(durable_report))
        except Exception as e:
            log_swallowed_error("core.agents.hallucination.streaming.persist_streaming_report", e)

    # --- Sprint C auto-persist (Neo4j artifact store) -----------------------
    # Mirrors the non-streaming /agent/hallucination endpoint's behavior so
    # the FE does not need to issue a redundant /verification/save call after
    # consuming the stream. Save happens AFTER the retry-sweep recount and
    # AFTER the consistency-check annotations are folded into collected_results,
    # so the persisted claims reflect the final state of the pipeline. We
    # skip persistence on interrupted / credit-exhausted / empty runs because
    # the claim set is partial and saving it would pollute the artifact store
    # with degraded data (matches the non-streaming ``result.get("skipped")``
    # gate). Failure is logged via log_swallowed_error but never blocks the
    # stream — the caller's claims are already in hand via the yielded events.
    persisted = False
    if (
        save_report_fn is not None
        and persist_report
        and not skip_durable  # E1 CR-019: don't clobber the Neo4j report on a bad-index retry
        and not stream_interrupted
        and not credit_exhausted
        and claims
    ):
        try:
            # E1 CR-019: durable_* is the merged N-claim report on a single-claim
            # retry, else the fresh run — matching the Redis hall:{cid} write above.
            save_report_fn(
                conversation_id=conversation_id,
                claims=durable_claims,
                overall_score=durable_overall,
                verified=durable_counts["verified"],
                unverified=durable_counts["unverified"],
                uncertain=durable_counts["uncertain"],
                total=durable_counts["total"],
            )
            persisted = True
        except Exception as exc:
            log_swallowed_error(
                "core.agents.hallucination.streaming.auto_persist",
                exc,
                context={"conversation_id": conversation_id},
                redis_client=redis_client,
            )
    if save_report_fn is not None:
        yield {"type": "persisted", "success": persisted}
