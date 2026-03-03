# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Curation Agent — artifact quality scoring + AI synopsis generation.

Scores every artifact on 4 dimensions (summary, keywords, freshness,
completeness), stores the composite quality_score on Neo4j Artifact
nodes, and returns a distribution report.  All scoring is pure math —
no LLM calls required.

Optionally generates AI synopses for artifacts with raw/truncated
summaries, using the free Llama model via Bifrost.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from typing import Any

import httpx

import config
from db.neo4j.artifacts import list_artifacts, update_artifact_summary
from middleware.request_id import tracing_headers
from utils.circuit_breaker import CircuitOpenError, get_breaker
from utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.curator")


# ---------------------------------------------------------------------------
# Individual scoring functions (pure, testable)
# ---------------------------------------------------------------------------

def score_summary(summary: str) -> float:
    """Score summary quality based on length heuristics. Returns [0, 1]."""
    if not summary or not summary.strip():
        return 0.0
    length = len(summary.strip())
    if length < config.QUALITY_SUMMARY_MIN_CHARS:
        return length / config.QUALITY_SUMMARY_MIN_CHARS
    if length <= config.QUALITY_SUMMARY_MAX_CHARS:
        return 1.0
    overshoot = length - config.QUALITY_SUMMARY_MAX_CHARS
    return max(0.3, 1.0 - (overshoot / 1000))


def score_keywords(keywords_json: str) -> float:
    """Score keyword quality based on count. Returns [0, 1]."""
    try:
        keywords = json.loads(keywords_json) if keywords_json else []
    except (json.JSONDecodeError, TypeError):
        keywords = []
    if not keywords:
        return 0.0
    count = len(keywords)
    if count >= config.QUALITY_KEYWORDS_OPTIMAL:
        return 1.0
    return count / config.QUALITY_KEYWORDS_OPTIMAL


def score_freshness(ingested_at: str, modified_at: str | None = None) -> float:
    """Score freshness using exponential decay. Returns [0, 1]."""
    timestamp = modified_at or ingested_at
    if not timestamp:
        return 0.5
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(timestamp)
        now = utcnow()
        # Handle tz-aware vs tz-naive comparison
        if dt.tzinfo is None:
            now = now.replace(tzinfo=None)
        age_days = (now - dt).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0
        return math.pow(2, -age_days / config.TEMPORAL_HALF_LIFE_DAYS)
    except (ValueError, TypeError):
        return 0.5


def score_completeness(artifact: dict[str, Any]) -> float:
    """Score metadata completeness. Returns [0, 1]."""
    checks = 0
    total = 4

    # Has a non-trivial summary?
    summary = artifact.get("summary", "")
    if summary and len(summary.strip()) >= 20:
        checks += 1

    # Has keywords?
    kw_json = artifact.get("keywords", "[]")
    try:
        kw = json.loads(kw_json) if kw_json else []
    except (json.JSONDecodeError, TypeError):
        kw = []
    if len(kw) >= 2:
        checks += 1

    # Has tags?
    tags_json = artifact.get("tags", "[]")
    try:
        tags = json.loads(tags_json) if tags_json else []
    except (json.JSONDecodeError, TypeError):
        tags = []
    if tags:
        checks += 1

    # Has non-default sub_category?
    sub_cat = artifact.get("sub_category", "")
    if sub_cat and sub_cat != config.DEFAULT_SUB_CATEGORY:
        checks += 1

    return checks / total


def compute_quality_score(artifact: dict[str, Any]) -> dict[str, Any]:
    """Compute weighted quality score for a single artifact."""
    s_summary = score_summary(artifact.get("summary", ""))
    s_keywords = score_keywords(artifact.get("keywords", "[]"))
    s_freshness = score_freshness(
        artifact.get("ingested_at", ""),
        artifact.get("modified_at"),
    )
    s_completeness = score_completeness(artifact)

    total = (
        config.QUALITY_WEIGHT_SUMMARY * s_summary
        + config.QUALITY_WEIGHT_KEYWORDS * s_keywords
        + config.QUALITY_WEIGHT_FRESHNESS * s_freshness
        + config.QUALITY_WEIGHT_COMPLETENESS * s_completeness
    )

    issues: list[str] = []
    if s_summary < 0.5:
        issues.append("summary_weak" if s_summary > 0 else "summary_missing")
    if s_keywords < 0.5:
        issues.append("keywords_sparse" if s_keywords > 0 else "keywords_missing")
    if s_completeness < 0.5:
        issues.append("metadata_incomplete")
    if s_freshness < 0.3:
        issues.append("stale")

    return {
        "quality_score": round(total, 4),
        "breakdown": {
            "summary": round(s_summary, 4),
            "keywords": round(s_keywords, 4),
            "freshness": round(s_freshness, 4),
            "completeness": round(s_completeness, 4),
        },
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Neo4j quality score persistence
# ---------------------------------------------------------------------------

def _store_quality_scores(
    neo4j_driver,
    scores: list[dict[str, Any]],
) -> int:
    """Batch-update quality_score on Artifact nodes. Returns count updated."""
    if not scores:
        return 0
    now = utcnow_iso()
    with neo4j_driver.session() as session:
        result = session.run(
            """
            UNWIND $items AS item
            MATCH (a:Artifact {id: item.id})
            SET a.quality_score = item.score,
                a.quality_scored_at = $now
            RETURN count(a) AS updated
            """,
            items=[{"id": s["artifact_id"], "score": s["quality_score"]} for s in scores],
            now=now,
        )
        record = result.single()
        return record["updated"] if record else 0


# ---------------------------------------------------------------------------
# Synopsis helpers
# ---------------------------------------------------------------------------

def _is_truncated_summary(summary: str) -> bool:
    """Detect raw/truncated summaries that need AI regeneration."""
    if not summary or not summary.strip():
        return True
    s = summary.strip()
    if len(s) < 50:
        return True
    # No sentence-ending punctuation — likely cut off mid-text
    if not re.search(r'[.!?]$', s):
        return True
    return False


async def _generate_synopsis(
    text: str,
    bifrost_url: str,
    model: str,
    max_input_chars: int,
    max_tokens: int,
    *,
    filename: str = "",
    domain: str = "",
) -> str:
    """Call Bifrost LLM to generate a concise synopsis. Returns empty string on failure.

    On 429 rate-limit, waits 60s and retries once. Free models on OpenRouter
    are limited to ~8 RPM, so the caller must also throttle between calls.
    """
    snippet = text[:max_input_chars]
    context = ""
    if filename or domain:
        parts = []
        if filename:
            parts.append(f"Filename: {filename}")
        if domain:
            parts.append(f"Domain: {domain}")
        context = " | ".join(parts) + "\n"
    prompt = (
        "Answer: What is this document about? Write 1-2 concise sentences.\n"
        "Rules:\n"
        "- State the specific subject matter and key content directly.\n"
        "- Do NOT start with 'This document' or 'This is'.\n"
        "- Include the most distinguishing detail (e.g. topic, date range, technology).\n\n"
        f"{context}"
        f"Content:\n{snippet}"
    )

    breaker = get_breaker("bifrost-synopsis")

    async def _bifrost_synopsis() -> dict:
        async with httpx.AsyncClient(timeout=config.BIFROST_TIMEOUT, headers=tracing_headers()) as client:
            resp = await client.post(
                f"{bifrost_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code == 429:
                raise httpx.HTTPStatusError(
                    "Rate limited", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.json()

    for attempt in range(2):
        try:
            data = await breaker.call(_bifrost_synopsis)
            content = data["choices"][0]["message"]["content"].strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content.rsplit("```", 1)[0]
            return content.strip()
        except CircuitOpenError:
            logger.warning("Bifrost synopsis circuit open, skipping synopsis generation")
            return ""
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt == 0:
                logger.info("Rate limited (429), waiting 60s before retry")
                await asyncio.sleep(60)
                continue
            logger.warning(f"Synopsis generation failed: {e}")
            return ""
        except Exception as e:
            logger.warning(f"Synopsis generation failed: {e}")
            return ""
    return ""


# ---------------------------------------------------------------------------
# Synopsis estimation
# ---------------------------------------------------------------------------

def estimate_synopsis_run(
    neo4j_driver,
    chroma_client,
    model: str,
    domains: list[str] | None = None,
    max_artifacts: int = 200,
) -> dict[str, Any]:
    """Count synopsis candidates and estimate cost/time for a given model."""
    target_domains = domains or config.DOMAINS
    candidate_count = 0

    for domain in target_domains:
        try:
            artifacts = list_artifacts(neo4j_driver, domain=domain, limit=max_artifacts)
        except Exception:
            continue
        candidates = [
            a for a in artifacts
            if _is_truncated_summary(a.get("summary", ""))
        ][:50]
        candidate_count += len(candidates)

    model_info = config.SYNOPSIS_MODEL_OPTIONS.get(model, {})
    throttle = model_info.get("throttle", 8.0)
    rpm = model_info.get("rpm", 8)
    label = model_info.get("label", model.split("/")[-1])
    input_per_1m = model_info.get("input_per_1m", 0.0)
    output_per_1m = model_info.get("output_per_1m", 0.0)

    # Cost estimate: ~500 input tokens + ~80 output tokens per synopsis
    avg_input_tokens = 500
    avg_output_tokens = 80
    estimated_cost = (
        (candidate_count * avg_input_tokens / 1_000_000) * input_per_1m
        + (candidate_count * avg_output_tokens / 1_000_000) * output_per_1m
    )

    estimated_seconds = candidate_count * throttle
    if estimated_seconds < 60:
        time_display = f"~{int(estimated_seconds)}s"
    else:
        time_display = f"~{int(estimated_seconds / 60)}m"

    return {
        "candidate_count": candidate_count,
        "model": model,
        "model_label": label,
        "estimated_cost_usd": round(estimated_cost, 4),
        "estimated_time_display": time_display,
        "rpm_limit": rpm,
        "is_free_model": input_per_1m == 0.0 and output_per_1m == 0.0,
    }


# ---------------------------------------------------------------------------
# Main curation function
# ---------------------------------------------------------------------------

async def curate(
    neo4j_driver,
    mode: str = "audit",
    domains: list[str] | None = None,
    max_artifacts: int = 200,
    chroma_client=None,
    generate_synopses: bool = False,
    synopsis_model: str | None = None,
) -> dict[str, Any]:
    """Score artifact quality across the knowledge base.

    Args:
        neo4j_driver: Neo4j driver instance.
        mode: "audit" (score and report, store scores).
        domains: Filter to specific domains (None = all).
        max_artifacts: Max artifacts to score per domain.
        chroma_client: ChromaDB client (required if generate_synopses=True).
        generate_synopses: If True, generate AI synopses for truncated summaries.
    """
    target_domains = domains or config.DOMAINS

    all_scores: list[dict[str, Any]] = []
    # Collect artifacts by domain for synopsis pass
    artifacts_by_domain: dict[str, list[dict[str, Any]]] = {}

    for domain in target_domains:
        try:
            artifacts = list_artifacts(neo4j_driver, domain=domain, limit=max_artifacts)
        except Exception as e:
            logger.warning(f"Failed to list artifacts for {domain}: {e}")
            continue

        if generate_synopses:
            artifacts_by_domain[domain] = artifacts

        for artifact in artifacts:
            report = compute_quality_score(artifact)
            all_scores.append({
                "artifact_id": artifact["id"],
                "filename": artifact["filename"],
                "domain": domain,
                "quality_score": report["quality_score"],
                "breakdown": report["breakdown"],
                "issues": report["issues"],
            })

    # Store scores in Neo4j
    updated = 0
    if all_scores:
        try:
            updated = _store_quality_scores(neo4j_driver, all_scores)
        except Exception as e:
            logger.error(f"Failed to store quality scores: {e}")

    # Synopsis generation pass
    synopses_generated = 0
    effective_model = synopsis_model or config.SYNOPSIS_MODEL
    model_info = config.SYNOPSIS_MODEL_OPTIONS.get(effective_model, {})
    throttle_delay = model_info.get("throttle", 8.0)

    if generate_synopses and chroma_client:
        for domain, artifacts in artifacts_by_domain.items():
            candidates = [
                a for a in artifacts
                if _is_truncated_summary(a.get("summary", ""))
            ][:50]  # cap per run
            if not candidates:
                continue
            try:
                collection = chroma_client.get_collection(
                    name=config.collection_name(domain)
                )
            except Exception as e:
                logger.warning(f"Cannot access collection for {domain}: {e}")
                continue

            for artifact in candidates:
                chunk_id = f"{artifact['id']}_chunk_0"
                try:
                    result = collection.get(ids=[chunk_id])
                    docs = result.get("documents", [])
                    if not docs or not docs[0]:
                        continue
                    text = docs[0]
                except Exception:
                    continue

                synopsis = await _generate_synopsis(
                    text,
                    config.BIFROST_URL,
                    effective_model,
                    config.SYNOPSIS_MAX_INPUT_CHARS,
                    config.SYNOPSIS_MAX_TOKENS,
                    filename=artifact.get("filename", ""),
                    domain=domain,
                )
                if synopsis:
                    try:
                        update_artifact_summary(neo4j_driver, artifact["id"], synopsis)
                        synopses_generated += 1
                    except Exception as e:
                        logger.warning(f"Failed to store synopsis for {artifact['id'][:8]}: {e}")
                # Adaptive throttle based on model rate limits
                await asyncio.sleep(throttle_delay)

        if synopses_generated:
            logger.info(f"Generated {synopses_generated} AI synopses")

    score_values = [s["quality_score"] for s in all_scores]
    avg_score = sum(score_values) / len(score_values) if score_values else 0.0

    # Low-quality artifacts for review
    low_quality = sorted(
        [s for s in all_scores if s["quality_score"] < 0.5],
        key=lambda x: x["quality_score"],
    )

    score_dist = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
    for s in score_values:
        tier = "excellent" if s >= 0.8 else "good" if s >= 0.6 else "fair" if s >= 0.4 else "poor"
        score_dist[tier] += 1

    return {
        "timestamp": utcnow_iso(),
        "mode": mode,
        "artifacts_scored": len(all_scores),
        "artifacts_stored": updated,
        "synopses_generated": synopses_generated,
        "avg_quality_score": round(avg_score, 4),
        "score_distribution": score_dist,
        "domains_scored": target_domains,
        "low_quality_artifacts": low_quality[:20],
    }
