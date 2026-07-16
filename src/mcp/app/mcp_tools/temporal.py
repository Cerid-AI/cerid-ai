# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 6 temporal + privacy/safety tools — 5 tools.

* ``pkb_timeline``       — chronological view of artifacts matching a query.
* ``pkb_trending``       — concepts heating up over a period.
* ``pkb_revisit_due``    — spaced-repetition prompt list.
* ``pkb_privacy_audit``  — scan KB for PII / credentials / sensitive content.
* ``pkb_quarantine``     — soft-delete with retention-window auto-purge.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import config
from app.deps import get_neo4j
from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
    register_tool,
)
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.mcp_tools.temporal")


def _period_to_delta(period: str) -> timedelta:
    units = {"h": "hours", "d": "days", "w": "weeks"}
    suffix = period[-1].lower() if period else ""
    if suffix not in units:
        raise InvalidParamsError(f"period must end in h/d/w; got {period!r}")
    try:
        n = int(period[:-1])
    except ValueError as e:
        raise InvalidParamsError(f"Invalid period {period!r}") from e
    return timedelta(**{units[suffix]: n})


# ============================================================ pkb_timeline


@register_tool(
    name="pkb_timeline",
    description=(
        "Chronological view of artifacts matching a query over a period, "
        "grouped by day/week/month. **Use when** the user wants a "
        "time-ordered narrative of related ingests (e.g. 'show me how "
        "Stripe-related work evolved this month'). **Returns** "
        "`{timeline: [{date, artifacts: [{id, filename, summary}]}], "
        "query, period, granularity, total_artifacts}`. Matches by "
        "filename + keywords + summary substring."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "period": {"type": "string", "default": "30d"},
            "granularity": {
                "type": "string",
                "enum": ["day", "week", "month"],
                "default": "day",
            },
            "max_artifacts": {"type": "integer", "default": 200},
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "timeline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "artifacts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "filename": {"type": "string"},
                                    "summary": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            "query": {"type": "string"},
            "period": {"type": "string"},
            "granularity": {"type": "string"},
            "total_artifacts": {"type": "integer"},
        },
    },
    cost_class="medium",
)
async def pkb_timeline(
    query: str,
    period: str = "30d",
    granularity: str = "day",
    max_artifacts: int = 200,
) -> dict[str, Any]:
    if granularity not in ("day", "week", "month"):
        raise InvalidParamsError(f"granularity must be day/week/month; got {granularity!r}")
    if not query.strip():
        raise InvalidParamsError("query must be non-empty")
    since = (datetime.now(timezone.utc) - _period_to_delta(period)).isoformat()

    driver = get_neo4j()

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (a:Artifact)
                WHERE a.ingested_at >= $since
                  AND coalesce(a.archived, false) = false
                  AND coalesce(a.flag_reason, '') = ''
                  AND (
                    toLower(coalesce(a.filename, '')) CONTAINS toLower($q)
                    OR toLower(coalesce(a.keywords, '')) CONTAINS toLower($q)
                    OR toLower(coalesce(a.summary, '')) CONTAINS toLower($q)
                  )
                WITH a, date.truncate('{granularity}', date(datetime(a.ingested_at))) AS bucket
                ORDER BY bucket, a.ingested_at
                WITH bucket, collect({{id: a.id, filename: coalesce(a.filename, ''),
                                      summary: coalesce(a.summary, '')}}) AS artifacts
                RETURN toString(bucket) AS date, artifacts
                ORDER BY date
                LIMIT $cap
                """,
                since=since, q=query, cap=max_artifacts,
            )
            return [dict(r) for r in result]

    try:
        rows = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    return {
        "timeline": rows,
        "query": query,
        "period": period,
        "granularity": granularity,
        "total_artifacts": sum(len(r.get("artifacts", [])) for r in rows),
    }


# ============================================================ pkb_trending


@register_tool(
    name="pkb_trending",
    description=(
        "Surface concepts (entities + tags) whose mention volume has "
        "spiked over a recent period vs the prior period of equal "
        "length. **Use when** the user wants a 'what's hot' digest. "
        "Returns top-k by relative growth. **Returns** `{trending: "
        "[{concept, current_count, prior_count, growth_factor}], "
        "domain, period, k}`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": f"Restrict to one domain ({', '.join(config.DOMAINS)}). Empty = all.",
                "default": "",
            },
            "period": {"type": "string", "default": "7d"},
            "k": {"type": "integer", "default": 10},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "trending": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "concept": {"type": "string"},
                        "current_count": {"type": "integer"},
                        "prior_count": {"type": "integer"},
                        "growth_factor": {"type": "number"},
                    },
                },
            },
            "domain": {"type": "string"},
            "period": {"type": "string"},
            "k": {"type": "integer"},
        },
    },
    cost_class="medium",
)
async def pkb_trending(
    domain: str = "",
    period: str = "7d",
    k: int = 10,
) -> dict[str, Any]:
    if domain and domain not in config.DOMAINS:
        raise InvalidParamsError(f"Invalid domain {domain!r}")
    k = max(1, min(int(k), 50))
    delta = _period_to_delta(period)
    now = datetime.now(timezone.utc)
    current_start = (now - delta).isoformat()
    prior_start = (now - 2 * delta).isoformat()

    domain_clause = "AND a.domain = $domain" if domain else ""

    driver = get_neo4j()

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (a:Artifact)-[:MENTIONS]->(e:Entity)
                WHERE coalesce(a.archived, false) = false {domain_clause}
                WITH e,
                     sum(CASE WHEN a.ingested_at >= $current_start THEN 1 ELSE 0 END) AS cur,
                     sum(CASE WHEN a.ingested_at >= $prior_start
                              AND a.ingested_at < $current_start THEN 1 ELSE 0 END) AS prior
                WHERE cur > 0
                WITH e, cur, prior,
                     // Growth factor: cur / max(prior, 1). New concepts
                     // get rank by cur*100 so they dominate the list.
                     toFloat(cur) / CASE WHEN prior = 0 THEN 0.01 ELSE toFloat(prior) END AS growth
                RETURN e.name AS concept, cur AS current_count, prior AS prior_count, growth AS growth_factor
                ORDER BY growth DESC, cur DESC
                LIMIT $k
                """,
                current_start=current_start, prior_start=prior_start,
                domain=domain or "", k=k,
            )
            return [dict(r) for r in result]

    try:
        rows = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    return {
        "trending": [
            {
                "concept": r["concept"],
                "current_count": int(r["current_count"]),
                "prior_count": int(r["prior_count"]),
                "growth_factor": round(float(r["growth_factor"]), 2),
            }
            for r in rows
        ],
        "domain": domain,
        "period": period,
        "k": k,
    }


# ============================================================ pkb_revisit_due


@register_tool(
    name="pkb_revisit_due",
    description=(
        "Spaced-repetition prompt list: artifacts whose `last_accessed_at` "
        "is past a forgetting-curve cutoff and have high relevance "
        "(endorsement_weight > 1.0 or recent recall_count). **Use when** "
        "surfacing 'what should I review now?' for the user. "
        "**Returns** `{due: [{artifact_id, filename, last_accessed_at, "
        "days_since, recall_priority}], total, generated_at}`. Default "
        "cap 20."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Restrict to one domain. Empty = all.",
                "default": "",
            },
            "max_results": {"type": "integer", "default": 20},
            "min_days_since": {
                "type": "integer",
                "description": "Minimum days since last access to be considered 'due'",
                "default": 14,
            },
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "due": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "filename": {"type": "string"},
                        "last_accessed_at": {"type": "string"},
                        "days_since": {"type": "integer"},
                        "recall_priority": {"type": "number"},
                    },
                },
            },
            "total": {"type": "integer"},
            "generated_at": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_revisit_due(
    domain: str = "",
    max_results: int = 20,
    min_days_since: int = 14,
) -> dict[str, Any]:
    if domain and domain not in config.DOMAINS:
        raise InvalidParamsError(f"Invalid domain {domain!r}")
    max_results = max(1, min(int(max_results), 100))
    min_days_since = max(1, int(min_days_since))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=min_days_since)).isoformat()

    domain_clause = "AND a.domain = $domain" if domain else ""

    driver = get_neo4j()

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (a:Artifact)
                WHERE coalesce(a.archived, false) = false
                  AND coalesce(a.flag_reason, '') = ''
                  AND coalesce(a.last_accessed_at, a.ingested_at) <= $cutoff
                  {domain_clause}
                WITH a,
                     coalesce(a.last_accessed_at, a.ingested_at) AS last_acc,
                     coalesce(a.endorsement_weight, 1.0) AS endorsement,
                     coalesce(a.recall_count, 0) AS recall_count
                WITH a, last_acc,
                     duration.between(datetime(last_acc), datetime()).days AS days_since,
                     endorsement, recall_count
                WHERE days_since >= $min_days
                // Recall priority: weight grows with days_since but plateaus
                // logarithmically; endorsement multiplies; recall_count tips
                // ties (already-touched items rank ahead of cold ones).
                WITH a, last_acc, days_since,
                     endorsement * (1 + log10(days_since + 1)) + toFloat(recall_count) * 0.1 AS priority
                RETURN
                    a.id AS artifact_id,
                    coalesce(a.filename, '') AS filename,
                    last_acc AS last_accessed_at,
                    days_since,
                    priority AS recall_priority
                ORDER BY priority DESC, days_since DESC
                LIMIT $cap
                """,
                cutoff=cutoff, min_days=min_days_since,
                domain=domain or "", cap=max_results,
            )
            return [dict(r) for r in result]

    try:
        rows = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    return {
        "due": [
            {
                "artifact_id": r["artifact_id"],
                "filename": r.get("filename") or "",
                "last_accessed_at": str(r["last_accessed_at"]),
                "days_since": int(r["days_since"]),
                "recall_priority": round(float(r["recall_priority"]), 3),
            }
            for r in rows
        ],
        "total": len(rows),
        "generated_at": utcnow_iso(),
    }


# ============================================================ pkb_privacy_audit


# PII regex patterns. Conservative — false-positives are acceptable
# (operator reviews findings) but false-negatives expose data.
_PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ssn_us": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone_us": re.compile(r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),  # noqa: DUO138 — {13,16} cap bounds backtracking
    "api_key_generic": re.compile(r"\b(sk-|pk-|api[_-]?key[=:]?\s*)[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_pem": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
}


@register_tool(
    name="pkb_privacy_audit",
    description=(
        "Scan a sample of the KB for PII / credentials / sensitive "
        "content. Patterns checked: email, US SSN, US phone, credit "
        "card, generic API keys (sk-, pk-, api_key=), AWS access keys, "
        "PEM private keys, JWTs. **Use when** auditing the KB for "
        "leaks before sharing / exporting. **Returns** `{findings: "
        "[{artifact_id, filename, pattern, count, sample}], "
        "artifacts_scanned, patterns_checked}`. Read-only — no "
        "mutations. Cap on artifacts scanned (default 500)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Restrict to one domain. Empty = all.",
                "default": "",
            },
            "max_artifacts": {"type": "integer", "default": 500},
            "patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subset of pattern names to check. Empty = all.",
            },
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "filename": {"type": "string"},
                        "pattern": {"type": "string"},
                        "count": {"type": "integer"},
                        "sample": {"type": "string"},
                    },
                },
            },
            "artifacts_scanned": {"type": "integer"},
            "patterns_checked": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    },
    cost_class="medium",
)
async def pkb_privacy_audit(
    domain: str = "",
    max_artifacts: int = 500,
    patterns: list[str] | None = None,
) -> dict[str, Any]:
    if domain and domain not in config.DOMAINS:
        raise InvalidParamsError(f"Invalid domain {domain!r}")
    max_artifacts = max(1, min(int(max_artifacts), 5000))

    pattern_subset = list(patterns or _PII_PATTERNS.keys())
    bad = set(pattern_subset) - set(_PII_PATTERNS.keys())
    if bad:
        raise InvalidParamsError(
            f"Unknown pattern(s): {sorted(bad)!r}. Valid: {sorted(_PII_PATTERNS.keys())}"
        )

    # Pull artifact summaries via the existing graph helper; that
    # gives us a small, scannable text representative per artifact
    # without paying the cost of full chunk fetch.
    from app.db import neo4j as graph

    driver = get_neo4j()

    def _list() -> list[dict[str, Any]]:
        return graph.list_artifacts(
            driver,
            domain=domain or None,
            limit=max_artifacts,
        )

    try:
        artifacts = await asyncio.to_thread(_list)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    findings: list[dict[str, Any]] = []
    for a in artifacts:
        # Build search corpus from filename + summary + keywords.
        # Full chunk content would be more thorough but multiplies
        # scan cost; if false-negatives surface, callers can pass a
        # smaller max_artifacts and we extend to chunks per-artifact.
        corpus_parts = [
            a.get("filename") or "",
            a.get("summary") or "",
            a.get("keywords") or "",
        ]
        corpus = "\n".join(p for p in corpus_parts if p)
        if not corpus:
            continue

        for pname in pattern_subset:
            matches = _PII_PATTERNS[pname].findall(corpus)
            if matches:
                # Redact the sample to first 30 chars so finding output
                # doesn't itself become a leak channel.
                sample_raw = matches[0] if isinstance(matches[0], str) else str(matches[0])
                sample = sample_raw[:30] + ("..." if len(sample_raw) > 30 else "")
                findings.append({
                    "artifact_id": a["id"],
                    "filename": a.get("filename") or "",
                    "pattern": pname,
                    "count": len(matches),
                    "sample": sample,
                })

    return {
        "findings": findings,
        "artifacts_scanned": len(artifacts),
        "patterns_checked": pattern_subset,
    }


# ============================================================ pkb_quarantine


@register_tool(
    name="pkb_quarantine",
    description=(
        "Soft-delete with retention window. Marks an artifact as "
        "quarantined (excluded from default retrieval) with an "
        "auto-purge cutoff `retention_days` in the future. The "
        "scheduled maintenance job will hard-delete past the cutoff. "
        "**Use when** removing an artifact reversibly until retention "
        "expires. Distinct from `pkb_artifact_delete(hard=false)` "
        "which has no expiry. **Returns** `{artifact_id, "
        "quarantined_at, purge_after, retention_days}`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "retention_days": {
                "type": "integer",
                "description": "Days before auto-purge (1-365)",
                "default": 90,
            },
            "reason": {
                "type": "string",
                "description": "Optional rationale (<=500 chars)",
                "default": "",
            },
        },
        "required": ["artifact_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "quarantined_at": {"type": "string"},
            "purge_after": {"type": "string"},
            "retention_days": {"type": "integer"},
            "reason": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_quarantine(
    artifact_id: str,
    retention_days: int = 90,
    reason: str = "",
) -> dict[str, Any]:
    if not (1 <= retention_days <= 365):
        raise InvalidParamsError("retention_days must be in [1, 365]")
    if len(reason) > 500:
        raise InvalidParamsError("reason must be <=500 chars")

    now = datetime.now(timezone.utc)
    purge_after = (now + timedelta(days=retention_days)).isoformat()
    now_iso = now.isoformat()

    driver = get_neo4j()

    # Route the archived write through the content-lifecycle coordinator, which
    # centralizes the ``a.archived = true`` write and busts the query-result
    # caches. Quarantine-specific fields ride along via ``extra_props`` (merged
    # with ``SET a += $extra``). ``quarantined_at`` was already a Python ISO
    # string (``now.isoformat()``), never a server-side ``datetime()``, so it is
    # safe to pass as a plain param and preserves the exact prior value;
    # ``archived_at`` is additionally set server-side by ``set_archived`` (the
    # soft-delete convention) — harmless to the scheduler, which reads
    # ``purge_after`` + ``archived``.
    extra_props = {
        "quarantined_at": now_iso,
        "purge_after": purge_after,
        "quarantine_reason": reason,
    }

    try:
        from app.services.content_lifecycle import hide_content
        ok = await asyncio.to_thread(
            hide_content, artifact_id, neo4j=driver, extra_props=extra_props
        )
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    if not ok:
        raise ResourceNotFoundError(f"Artifact {artifact_id!r} not found")

    return {
        "artifact_id": artifact_id,
        "quarantined_at": now_iso,
        "purge_after": purge_after,
        "retention_days": retention_days,
        "reason": reason,
    }
