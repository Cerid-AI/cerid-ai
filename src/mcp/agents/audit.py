# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audit Agent — operation tracking, cost estimation, and usage analytics."""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from utils.cache import REDIS_CONV_METRICS_PREFIX, REDIS_VERIFICATION_METRICS_KEY, get_log
from utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.audit")

# ---------------------------------------------------------------------------
# Cost estimation constants (approximate, OpenRouter pricing)
# ---------------------------------------------------------------------------
COST_PER_1K_TOKENS = {
    "smart": 0.0,       # Llama 3.1 free tier
    "pro": 0.003,       # Claude Sonnet
    "rerank": 0.0,      # Llama 3.1 free tier for reranking
}

# Average tokens per operation (estimates)
AVG_TOKENS = {
    "categorize_smart": 400,
    "categorize_pro": 400,
    "rerank": 300,
    "query": 0,          # queries themselves are free (embedding-based)
}


# ---------------------------------------------------------------------------
# Audit trail analysis
# ---------------------------------------------------------------------------

def get_activity_summary(
    redis_client,
    hours: int = 24,
    limit: int = 500,
) -> dict[str, Any]:
    """Generate an activity summary from the Redis audit log."""
    entries = get_log(redis_client, limit=limit)
    cutoff = (utcnow().replace(tzinfo=None) - timedelta(hours=hours)).isoformat()

    recent = [e for e in entries if e.get("timestamp", "") >= cutoff]

    event_counts = Counter(e.get("event", "unknown") for e in recent)
    domain_counts = Counter(e.get("domain", "unknown") for e in recent)

    hourly: dict[str, int] = defaultdict(int)
    hourly_by_type: dict[str, Counter] = defaultdict(Counter)
    for e in recent:
        ts = e.get("timestamp", "")
        if ts:
            hour_key = ts[:13]  # YYYY-MM-DDTHH
            hourly[hour_key] += 1
            event = e.get("event", "unknown")
            hourly_by_type[hour_key][event] += 1

    failures = [
        e for e in recent
        if e.get("event", "").endswith("_error") or "error" in e.get("event", "")
    ]

    return {
        "time_window_hours": hours,
        "total_events": len(recent),
        "event_breakdown": dict(event_counts),
        "domain_breakdown": dict(domain_counts),
        "hourly_timeline": dict(sorted(hourly.items())),
        "hourly_by_type": {k: dict(v) for k, v in sorted(hourly_by_type.items())},
        "recent_failures": failures[:10],
        "scanned_entries": len(entries),
    }


def get_ingestion_stats(
    redis_client,
    limit: int = 1000,
) -> dict[str, Any]:
    """Compute detailed ingestion statistics from the audit log."""
    entries = get_log(redis_client, limit=limit)

    ingests = [e for e in entries if e.get("event") == "ingest"]
    duplicates = [e for e in entries if e.get("event") == "duplicate" or e.get("status") == "duplicate"]
    recategorizations = [e for e in entries if e.get("event") == "recategorize"]

    domain_dist = Counter(e.get("domain", "unknown") for e in ingests)
    ext_dist: Counter[str] = Counter()
    for e in ingests:
        filename = e.get("filename", "")
        if "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            ext_dist[ext] += 1

    chunks_list = [e.get("chunks", 0) for e in ingests if "chunks" in e]
    avg_chunks = sum(chunks_list) / len(chunks_list) if chunks_list else 0

    total_attempts = len(ingests) + len(duplicates)
    dup_rate = len(duplicates) / total_attempts if total_attempts > 0 else 0.0

    return {
        "total_ingests": len(ingests),
        "total_duplicates": len(duplicates),
        "duplicate_rate": round(dup_rate, 3),
        "recategorizations": len(recategorizations),
        "domain_distribution": dict(domain_dist),
        "file_type_distribution": dict(ext_dist),
        "avg_chunks_per_file": round(avg_chunks, 1),
    }


def estimate_costs(
    redis_client,
    hours: int = 720,  # 30 days default
    limit: int = 5000,
) -> dict[str, Any]:
    """Estimate AI token usage and cost from the audit trail."""
    entries = get_log(redis_client, limit=limit)
    cutoff = (utcnow().replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
    recent = [e for e in entries if e.get("timestamp", "") >= cutoff]

    categorize_smart = sum(
        1 for e in recent
        if e.get("event") == "ingest" and e.get("categorize_mode") == "smart"
    )
    categorize_pro = sum(
        1 for e in recent
        if e.get("event") == "ingest" and e.get("categorize_mode") == "pro"
    )
    rerank_calls = sum(
        1 for e in recent
        if e.get("event") == "query" or e.get("event") == "agent_query"
    )

    smart_tokens = categorize_smart * AVG_TOKENS["categorize_smart"]
    pro_tokens = categorize_pro * AVG_TOKENS["categorize_pro"]
    rerank_tokens = rerank_calls * AVG_TOKENS["rerank"]
    total_tokens = smart_tokens + pro_tokens + rerank_tokens

    smart_cost = (smart_tokens / 1000) * COST_PER_1K_TOKENS["smart"]
    pro_cost = (pro_tokens / 1000) * COST_PER_1K_TOKENS["pro"]
    rerank_cost = (rerank_tokens / 1000) * COST_PER_1K_TOKENS["rerank"]
    total_cost = smart_cost + pro_cost + rerank_cost

    return {
        "time_window_hours": hours,
        "operations": {
            "categorize_smart": categorize_smart,
            "categorize_pro": categorize_pro,
            "rerank": rerank_calls,
        },
        "estimated_tokens": {
            "smart": smart_tokens,
            "pro": pro_tokens,
            "rerank": rerank_tokens,
            "total": total_tokens,
        },
        "estimated_cost_usd": {
            "smart": round(smart_cost, 4),
            "pro": round(pro_cost, 4),
            "rerank": round(rerank_cost, 4),
            "total": round(total_cost, 4),
        },
    }


def get_query_patterns(
    redis_client,
    limit: int = 500,
) -> dict[str, Any]:
    """Analyze query patterns from the audit log."""
    entries = get_log(redis_client, limit=limit)
    queries = [e for e in entries if e.get("event") in ("query", "agent_query")]

    domain_freq: Counter[str] = Counter()
    for q in queries:
        domain_val = q.get("domain", "")
        if "," in domain_val:
            for d in domain_val.split(","):
                domain_freq[d.strip()] += 1
        elif domain_val:
            domain_freq[domain_val] += 1

    result_counts = [q.get("results", 0) for q in queries if "results" in q]
    avg_results = sum(result_counts) / len(result_counts) if result_counts else 0

    return {
        "total_queries": len(queries),
        "domain_frequency": dict(domain_freq),
        "avg_results_per_query": round(avg_results, 1),
    }


# ---------------------------------------------------------------------------
# Conversation analytics
# ---------------------------------------------------------------------------

# Per-model cost rates (USD per 1K tokens, OpenRouter pricing)
MODEL_COST_RATES = {
    "anthropic/claude-sonnet-4": {"input": 0.003, "output": 0.015},
    "openai/gpt-4o": {"input": 0.0025, "output": 0.010},
    "openai/gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "google/gemini-2.5-flash": {"input": 0.00015, "output": 0.0006},
    "x-ai/grok-4-fast": {"input": 0.003, "output": 0.015},
    "meta-llama/llama-3.3-70b-instruct": {"input": 0.00012, "output": 0.0003},
}


def get_conversation_analytics(
    redis_client,
    limit: int = 100,
) -> dict[str, Any]:
    """Aggregate conversation metrics across all tracked conversations."""
    try:
        keys = []
        for key in redis_client.scan_iter(
            match=f"{REDIS_CONV_METRICS_PREFIX}*:metrics", count=100
        ):
            keys.append(key)
            if len(keys) >= limit:
                break
    except Exception as e:
        logger.warning(f"Failed to scan conversation metrics: {e}")
        return {"total_conversations": 0, "total_turns": 0, "models": {}, "total_cost_usd": 0.0}

    model_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "turns": 0, "input_tokens": 0, "output_tokens": 0,
        "total_latency_ms": 0, "cost_usd": 0.0,
    })
    total_turns = 0

    for key in keys:
        try:
            entries = redis_client.lrange(key, 0, -1)
            for raw in entries:
                entry = json.loads(raw)
                model = entry.get("model", "unknown")
                model_key = model.replace("openrouter/", "")
                inp = entry.get("input_tokens", 0)
                out = entry.get("output_tokens", 0)
                lat = entry.get("latency_ms", 0)

                stats = model_stats[model_key]
                stats["turns"] += 1
                stats["input_tokens"] += inp
                stats["output_tokens"] += out
                stats["total_latency_ms"] += lat

                rates = MODEL_COST_RATES.get(model_key, {"input": 0.001, "output": 0.005})
                stats["cost_usd"] += (inp / 1000) * rates["input"] + (out / 1000) * rates["output"]
                total_turns += 1
        except Exception as e:
            logger.debug(f"Failed to parse conversation metric entry: {e}")
            continue

    models_out = {}
    total_cost = 0.0
    for model_key, stats in model_stats.items():
        stats["cost_usd"] = round(stats["cost_usd"], 4)
        stats["avg_latency_ms"] = round(stats["total_latency_ms"] / stats["turns"]) if stats["turns"] else 0
        del stats["total_latency_ms"]
        models_out[model_key] = stats
        total_cost += stats["cost_usd"]

    return {
        "total_conversations": len(keys),
        "total_turns": total_turns,
        "models": models_out,
        "total_cost_usd": round(total_cost, 4),
    }


# ---------------------------------------------------------------------------
# Verification analytics
# ---------------------------------------------------------------------------

def get_verification_analytics(
    redis_client,
    hours: int = 24,
) -> dict[str, Any]:
    """Aggregate verification metrics for the accuracy dashboard."""
    cutoff = (utcnow().replace(tzinfo=None) - timedelta(hours=hours)).isoformat()

    try:
        raw_entries = redis_client.lrange(REDIS_VERIFICATION_METRICS_KEY, 0, -1)
    except Exception as e:
        logger.warning(f"Failed to read verification metrics: {e}")
        raw_entries = []

    entries = []
    for raw in raw_entries:
        try:
            entry = json.loads(raw)
            if entry.get("timestamp", "") >= cutoff:
                entries.append(entry)
        except Exception:
            continue

    if not entries:
        return {
            "total_checks": 0,
            "avg_accuracy": 0.0,
            "by_model": {},
            "hourly_accuracy": {},
        }

    model_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "checks": 0, "verified": 0, "unverified": 0, "uncertain": 0, "total_claims": 0,
    })
    # Track which verification models were used (separate from generating model)
    verify_model_counts: dict[str, int] = defaultdict(int)
    total_accuracy_sum = 0.0

    for entry in entries:
        model = entry.get("model", "unknown").replace("openrouter/", "")
        stats = model_stats[model]
        stats["checks"] += 1
        stats["verified"] += entry.get("verified", 0)
        stats["unverified"] += entry.get("unverified", 0)
        stats["uncertain"] += entry.get("uncertain", 0)
        stats["total_claims"] += entry.get("total", 0)
        total_accuracy_sum += entry.get("accuracy", 0.0)
        # Aggregate verification model usage
        for vm in entry.get("verification_models", []):
            verify_model_counts[vm.replace("openrouter/", "")] += 1

    by_model = {}
    for model, stats in model_stats.items():
        total = stats["total_claims"]
        by_model[model] = {
            "checks": stats["checks"],
            "accuracy": round(stats["verified"] / total, 4) if total > 0 else 0.0,
            "verified": stats["verified"],
            "unverified": stats["unverified"],
            "uncertain": stats["uncertain"],
        }

    hourly_buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"verified": 0, "total": 0})
    for entry in entries:
        ts = entry.get("timestamp", "")
        if ts:
            hour_key = ts[:13]
            hourly_buckets[hour_key]["verified"] += entry.get("verified", 0)
            hourly_buckets[hour_key]["total"] += entry.get("total", 0)

    hourly_accuracy = {}
    for hour, counts in sorted(hourly_buckets.items()):
        if counts["total"] > 0:
            hourly_accuracy[hour] = round(counts["verified"] / counts["total"], 4)

    # --- User feedback stats (thumbs-up/down on individual claims) ---
    feedback_stats = {"total": 0, "correct": 0, "incorrect": 0}
    try:
        from utils.cache import REDIS_VERIFICATION_FEEDBACK_KEY
        raw_feedback = redis_client.lrange(REDIS_VERIFICATION_FEEDBACK_KEY, 0, -1)
        for raw in raw_feedback:
            try:
                fb = json.loads(raw)
                if fb.get("timestamp", "") >= cutoff:
                    feedback_stats["total"] += 1
                    if fb.get("correct"):
                        feedback_stats["correct"] += 1
                    else:
                        feedback_stats["incorrect"] += 1
            except Exception:
                continue
    except Exception as e:
        logger.debug("Failed to read verification feedback: %s", e)

    # --- Verification error stats ---
    error_stats: dict[str, int] = defaultdict(int)
    recent_errors: list[dict[str, Any]] = []
    try:
        from utils.cache import REDIS_VERIFICATION_ERRORS_KEY
        raw_errors = redis_client.lrange(REDIS_VERIFICATION_ERRORS_KEY, 0, -1)
        for raw in raw_errors:
            try:
                err = json.loads(raw)
                if err.get("timestamp", "") >= cutoff:
                    error_stats[err.get("error_type", "unknown")] += 1
                    recent_errors.append(err)
            except Exception:
                continue
        # Keep only last 10 recent errors for the response
        recent_errors = recent_errors[-10:]
    except Exception as e:
        logger.debug("Failed to read verification errors: %s", e)

    return {
        "total_checks": len(entries),
        "avg_accuracy": round(total_accuracy_sum / len(entries), 4) if entries else 0.0,
        "by_model": by_model,
        "hourly_accuracy": hourly_accuracy,
        "verification_models": dict(verify_model_counts) if verify_model_counts else {},
        "user_feedback": feedback_stats if feedback_stats["total"] > 0 else None,
        "verification_errors": dict(error_stats) if error_stats else {},
        "recent_errors": recent_errors if recent_errors else [],
    }


# ---------------------------------------------------------------------------
# Main audit function
# ---------------------------------------------------------------------------

async def audit(
    redis_client,
    reports: list[str] | None = None,
    hours: int = 24,
) -> dict[str, Any]:
    """Run audit reports on knowledge base operations."""
    all_reports = {"activity", "ingestion", "costs", "queries", "conversations", "verification"}
    if reports is None:
        reports = list(all_reports)

    result: dict[str, Any] = {
        "timestamp": utcnow_iso(),
        "reports_generated": reports,
    }

    if "activity" in reports:
        result["activity"] = get_activity_summary(redis_client, hours=hours)

    if "ingestion" in reports:
        result["ingestion"] = get_ingestion_stats(redis_client)

    if "costs" in reports:
        result["costs"] = estimate_costs(redis_client)

    if "queries" in reports:
        result["queries"] = get_query_patterns(redis_client)

    if "conversations" in reports:
        result["conversations"] = get_conversation_analytics(redis_client)

    if "verification" in reports:
        result["verification"] = get_verification_analytics(redis_client, hours=hours)

    return result
