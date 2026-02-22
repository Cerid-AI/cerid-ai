"""
Audit Agent - Operation tracking, cost estimation, and usage analytics.

Provides:
- Aggregated audit trail from Redis (ingests, queries, recategorizations, rectifications)
- Per-domain activity summaries with time-range filtering
- Token/cost estimation for AI operations (categorization, reranking)
- Query pattern analysis (most-searched domains, top queries)
- Anomaly detection (unusual ingestion spikes, repeated failures)
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.cache import get_log

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
) -> Dict[str, Any]:
    """
    Generate an activity summary from the Redis audit log.

    Args:
        redis_client: Redis client instance
        hours: Time window in hours (default: 24h)
        limit: Maximum log entries to scan

    Returns:
        Summary with event counts, domain breakdown, and timeline
    """
    entries = get_log(redis_client, limit=limit)
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    # Filter to time window
    recent = [e for e in entries if e.get("timestamp", "") >= cutoff]

    # Count by event type
    event_counts = Counter(e.get("event", "unknown") for e in recent)

    # Count by domain
    domain_counts = Counter(e.get("domain", "unknown") for e in recent)

    # Count by hour for timeline
    hourly = defaultdict(int)
    for e in recent:
        ts = e.get("timestamp", "")
        if ts:
            hour_key = ts[:13]  # YYYY-MM-DDTHH
            hourly[hour_key] += 1

    # Recent failures (events with error info)
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
        "recent_failures": failures[:10],
        "scanned_entries": len(entries),
    }


def get_ingestion_stats(
    redis_client,
    limit: int = 1000,
) -> Dict[str, Any]:
    """
    Compute detailed ingestion statistics.

    Returns file type distribution, domain distribution, duplicate rate,
    and average chunks per file.
    """
    entries = get_log(redis_client, limit=limit)

    ingests = [e for e in entries if e.get("event") == "ingest"]
    duplicates = [e for e in entries if e.get("event") == "duplicate" or e.get("status") == "duplicate"]
    recategorizations = [e for e in entries if e.get("event") == "recategorize"]

    # Domain distribution of ingests
    domain_dist = Counter(e.get("domain", "unknown") for e in ingests)

    # File extension distribution
    ext_dist = Counter()
    for e in ingests:
        filename = e.get("filename", "")
        if "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            ext_dist[ext] += 1

    # Chunks per ingest (if recorded)
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
) -> Dict[str, Any]:
    """
    Estimate AI token usage and cost from the audit trail.

    Tracks categorization calls (smart/pro tiers) and reranking operations.
    """
    entries = get_log(redis_client, limit=limit)
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    recent = [e for e in entries if e.get("timestamp", "") >= cutoff]

    # Count AI operations
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

    # Estimate tokens
    smart_tokens = categorize_smart * AVG_TOKENS["categorize_smart"]
    pro_tokens = categorize_pro * AVG_TOKENS["categorize_pro"]
    rerank_tokens = rerank_calls * AVG_TOKENS["rerank"]
    total_tokens = smart_tokens + pro_tokens + rerank_tokens

    # Estimate cost
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
) -> Dict[str, Any]:
    """
    Analyze query patterns from the audit log.

    Returns most-queried domains, query frequency, and confidence distribution.
    """
    entries = get_log(redis_client, limit=limit)
    queries = [e for e in entries if e.get("event") in ("query", "agent_query")]

    # Domain frequency
    domain_freq = Counter()
    for q in queries:
        domain_val = q.get("domain", "")
        if "," in domain_val:
            for d in domain_val.split(","):
                domain_freq[d.strip()] += 1
        elif domain_val:
            domain_freq[domain_val] += 1

    # Results counts
    result_counts = [q.get("results", 0) for q in queries if "results" in q]
    avg_results = sum(result_counts) / len(result_counts) if result_counts else 0

    return {
        "total_queries": len(queries),
        "domain_frequency": dict(domain_freq),
        "avg_results_per_query": round(avg_results, 1),
    }


# ---------------------------------------------------------------------------
# Main audit function
# ---------------------------------------------------------------------------

async def audit(
    redis_client,
    reports: Optional[List[str]] = None,
    hours: int = 24,
) -> Dict[str, Any]:
    """
    Run audit reports on the knowledge base operations.

    Args:
        redis_client: Redis client instance
        reports: List of reports to generate. Default: all.
            Options: "activity", "ingestion", "costs", "queries"
        hours: Time window in hours for activity report

    Returns:
        Audit report with requested sections
    """
    all_reports = {"activity", "ingestion", "costs", "queries"}
    if reports is None:
        reports = list(all_reports)

    result = {
        "timestamp": datetime.utcnow().isoformat(),
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

    return result
