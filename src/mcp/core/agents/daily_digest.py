# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Daily digest agent — Phase K Day 1.

Generates an LLM-summarized "what happened in the last N hours"
digest pulling from:

  * Recent artifact ingests via ``list_artifacts(since=...)``
  * Curator-flagged content (quality_score below threshold)
  * Triaged-inbox urgent + actionable items (when Phase J is active)

The output is a structured ``DigestResult`` with five sections:

  - top_categories: ranked summary of which domains saw activity
  - key_threads: short list of standout artifacts (high-signal)
  - urgent: items the user should pay attention to today
  - action_items: extracted next-steps the user owes
  - quality_alerts: curator findings (artifacts that look broken)

Persisted as a single KB artifact in ``domain="digests"`` so the
chat surface + Subjects pane can retrieve and link to it.

Feature gate: ``daily_digest`` (Pro tier). Returns an empty result
with ``skipped=True`` when the flag is off.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.daily_digest")


# ── shape ─────────────────────────────────────────────────────────────

@dataclass
class DigestSection:
    title: str
    body: str
    artifact_ids: list[str] = field(default_factory=list)


@dataclass
class DigestResult:
    digest_id: str = ""
    generated_at: str = ""
    window_hours: int = 24
    top_categories: list[dict[str, Any]] = field(default_factory=list)
    key_threads: list[DigestSection] = field(default_factory=list)
    urgent: list[DigestSection] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    quality_alerts: list[DigestSection] = field(default_factory=list)
    artifact_count: int = 0
    flagged_count: int = 0
    inbox_urgent_count: int = 0
    skipped: bool = False
    skip_reason: str = ""
    persisted_artifact_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest_id": self.digest_id,
            "generated_at": self.generated_at,
            "window_hours": self.window_hours,
            "top_categories": self.top_categories,
            "key_threads": [asdict(s) for s in self.key_threads],
            "urgent": [asdict(s) for s in self.urgent],
            "action_items": self.action_items,
            "quality_alerts": [asdict(s) for s in self.quality_alerts],
            "artifact_count": self.artifact_count,
            "flagged_count": self.flagged_count,
            "inbox_urgent_count": self.inbox_urgent_count,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "persisted_artifact_id": self.persisted_artifact_id,
        }


# ── LLM prompt ────────────────────────────────────────────────────────

_DIGEST_PROMPT = """\
You are summarizing the last {window_hours} hours of activity in a personal knowledge base.
You will produce a SINGLE JSON object with exactly these keys:

  top_categories: array of objects {{"domain": str, "count": int, "highlight": str}}
                  (one per domain that saw activity, max 5; highlight is a one-line note)
  key_threads:    array of objects {{"title": str, "body": str, "artifact_ids": [str]}}
                  (max 5 standout artifacts the user should care about)
  urgent:         array of same shape as key_threads — items needing same-day attention
  action_items:   array of short strings — concrete next-steps the user owes
                  (max 10; each ≤ 100 chars)
  quality_alerts: array of same shape as key_threads — curator findings that suggest
                  broken/incomplete artifacts

Be selective. The user opens this once per morning — be high signal, not exhaustive.
If an entire section is empty (e.g. no urgent items today), return [] for that section.

Recent activity:
{activity_snapshot}

Curator-flagged (low quality_score):
{flagged_snapshot}

Inbox-triaged urgent + actionable (last {window_hours}h):
{inbox_snapshot}

Return ONLY the JSON object. No prose, no markdown fences.
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.S)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")  # greedy — digest JSON is one big object


# ── data fetchers ─────────────────────────────────────────────────────

def _fetch_recent_artifacts(driver: Any, since_iso: str, limit: int = 200) -> list[dict[str, Any]]:
    try:
        from app.db import neo4j as graph_db
        return graph_db.list_artifacts(driver, since=since_iso, limit=limit) or []
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("daily_digest._fetch_recent", exc)
        return []


def _fetch_flagged_artifacts(
    driver: Any,
    since_iso: str,
    quality_threshold: float,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Pull artifacts in the window with quality_score below threshold —
    the closest existing signal Cerid has for "curator-flagged"."""
    try:
        from app.db import neo4j as graph_db
        # list_artifacts supports min_quality (≥), not max. We pull
        # everything in the window then filter in Python — small N.
        all_recent = graph_db.list_artifacts(driver, since=since_iso, limit=500) or []
        return [
            a for a in all_recent
            if isinstance(a.get("quality_score"), (int, float))
            and a["quality_score"] < quality_threshold
        ][:limit]
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("daily_digest._fetch_flagged", exc)
        return []


def _fetch_inbox_urgent(driver: Any, since_iso: str, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch Phase J triaged-inbox urgent + actionable items in window."""
    try:
        from app.db import neo4j as graph_db
        recent = graph_db.list_artifacts(driver, domain="inbox", since=since_iso, limit=limit) or []
        return [
            a for a in recent
            if (a.get("tags") or {}).get("category") in ("urgent", "actionable")
        ]
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("daily_digest._fetch_inbox", exc)
        return []


# ── snapshot builders ─────────────────────────────────────────────────

_MAX_ARTIFACT_CONTEXT_PER_ITEM = 280  # chars
_MAX_TOTAL_SNAPSHOT_CHARS = 6000


def _build_activity_snapshot(artifacts: list[dict[str, Any]]) -> str:
    """Compact one-line-per-artifact summary that fits the LLM budget."""
    lines: list[str] = []
    for a in artifacts:
        domain = a.get("domain", "general")
        title = (a.get("filename") or a.get("title") or "(untitled)")[:80]
        summary = (a.get("summary") or "")[:200]
        line = f"- [{domain}] {title}"
        if summary:
            line += f" — {summary}"
        lines.append(line[:_MAX_ARTIFACT_CONTEXT_PER_ITEM])
    return _truncate("\n".join(lines), _MAX_TOTAL_SNAPSHOT_CHARS)


def _build_flagged_snapshot(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "(no quality alerts)"
    lines = []
    for a in artifacts:
        score = a.get("quality_score", 0.0)
        title = (a.get("filename") or a.get("title") or "(untitled)")[:80]
        lines.append(f"- {title} (quality_score={score:.2f})")
    return _truncate("\n".join(lines), 2000)


def _build_inbox_snapshot(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "(no urgent/actionable inbox threads)"
    lines = []
    for a in artifacts:
        tags = a.get("tags") or {}
        cat = tags.get("category", "?")
        subject = tags.get("subject") or a.get("filename") or "(no subject)"
        summary = tags.get("summary") or ""
        lines.append(f"- [{cat}] {subject[:80]} — {summary[:160]}")
    return _truncate("\n".join(lines), 2000)


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars - 3] + "..."


# ── category roll-up ──────────────────────────────────────────────────

def _compute_top_categories(artifacts: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Count artifacts per domain. Returns sorted-by-count list."""
    counts: dict[str, int] = {}
    for a in artifacts:
        domain = a.get("domain") or "general"
        counts[domain] = counts.get(domain, 0) + 1
    sorted_pairs = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {"domain": d, "count": c, "highlight": ""}
        for d, c in sorted_pairs[:limit]
    ]


# ── LLM call + parser ────────────────────────────────────────────────

async def _call_llm(prompt: str) -> str | None:
    from core.utils.internal_llm import call_internal_llm
    try:
        return await call_internal_llm(
            [{"role": "user", "content": prompt}],
            stage="daily_digest",
            temperature=0.2,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("daily_digest._call_llm", exc)
        return None


def _parse_llm_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    cleaned = _FENCE_RE.sub("", raw.strip())
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        match = _JSON_OBJECT_RE.search(cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except (ValueError, TypeError):
                return {}
    return {}


def _coerce_sections(raw: Any) -> list[DigestSection]:
    """Tolerant — accepts dicts or strings, clamps + populates defaults."""
    if not isinstance(raw, list):
        return []
    out: list[DigestSection] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", ""))[:120]
        body = str(entry.get("body", ""))[:600]
        artifact_ids = entry.get("artifact_ids", [])
        if isinstance(artifact_ids, list):
            ids = [str(x) for x in artifact_ids if x][:5]
        else:
            ids = []
        if title or body:
            out.append(DigestSection(title=title or "(untitled)", body=body, artifact_ids=ids))
    return out[:5]


# ── persistence ───────────────────────────────────────────────────────

async def _persist(result: DigestResult, mcp_base_url: str) -> str | None:
    """Persist the digest as a KB artifact via /ingest/structured."""
    import httpx

    parts: list[str] = [f"# Daily Digest — {result.generated_at[:10]}", ""]
    if result.top_categories:
        parts.append("## Top categories")
        for c in result.top_categories:
            parts.append(f"- **{c['domain']}** ({c['count']}) — {c.get('highlight', '')}")
        parts.append("")
    if result.urgent:
        parts.append("## Urgent")
        for s in result.urgent:
            parts.append(f"- **{s.title}** — {s.body}")
        parts.append("")
    if result.key_threads:
        parts.append("## Key threads")
        for s in result.key_threads:
            parts.append(f"- **{s.title}** — {s.body}")
        parts.append("")
    if result.action_items:
        parts.append("## Action items")
        for a in result.action_items:
            parts.append(f"- {a}")
        parts.append("")
    if result.quality_alerts:
        parts.append("## Quality alerts")
        for s in result.quality_alerts:
            parts.append(f"- **{s.title}** — {s.body}")

    content = "\n".join(parts)
    payload = {
        "content": content,
        "domain": "digests",
        "source_id": f"daily_digest:{result.generated_at[:10]}",
        "metadata": {
            "source": "daily_digest",
            "kind": "daily",
            "generated_at": result.generated_at,
            "window_hours": str(result.window_hours),
            "artifact_count": str(result.artifact_count),
            "flagged_count": str(result.flagged_count),
            "inbox_urgent_count": str(result.inbox_urgent_count),
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{mcp_base_url}/ingest/structured",
                json=payload,
                headers={"X-Client-ID": "daily_digest"},
            )
        if resp.status_code != 200:
            logger.warning("daily_digest persist returned %d", resp.status_code)
            return None
        body = resp.json()
        return body.get("artifact_id") or body.get("id")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("daily_digest.persist", exc)
        return None


# ── public entry ──────────────────────────────────────────────────────

DEFAULT_QUALITY_FLAG_THRESHOLD = 0.5


async def generate_daily_digest(
    *,
    window_hours: int = 24,
    quality_threshold: float = DEFAULT_QUALITY_FLAG_THRESHOLD,
    persist: bool = True,
    mcp_base_url: str | None = None,
) -> DigestResult:
    """Run one digest generation pass.

    Args:
        window_hours: lookback window. Default 24 = last day.
        quality_threshold: artifacts with quality_score below this
            count as flagged. Default 0.5.
        persist: when False, skip the KB write-back (useful for
            preview / dry-run from chat).
        mcp_base_url: override for /ingest/structured target. Defaults
            to the local MCP base.

    Feature-gated by ``daily_digest`` (Pro tier). Returns a result
    with ``skipped=True`` when the flag is off.
    """
    from config.features import is_feature_enabled

    result = DigestResult(
        digest_id=str(uuid.uuid4()),
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        window_hours=window_hours,
    )

    if not is_feature_enabled("daily_digest"):
        result.skipped = True
        result.skip_reason = "feature_gated"
        return result

    # Resolve neo4j driver — soft-skip if unavailable
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("daily_digest.get_neo4j", exc)
        result.skipped = True
        result.skip_reason = "neo4j_unavailable"
        return result

    since = (datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)).isoformat()

    # Three parallel reads
    artifacts, flagged, inbox = await asyncio.gather(
        asyncio.to_thread(_fetch_recent_artifacts, driver, since),
        asyncio.to_thread(_fetch_flagged_artifacts, driver, since, quality_threshold),
        asyncio.to_thread(_fetch_inbox_urgent, driver, since),
    )

    result.artifact_count = len(artifacts)
    result.flagged_count = len(flagged)
    result.inbox_urgent_count = len(inbox)
    result.top_categories = _compute_top_categories(artifacts)

    if not artifacts and not flagged and not inbox:
        # Nothing happened — return a minimal-but-explicit digest
        # so the user gets the "zero-activity" signal rather than
        # silence.
        result.action_items = []
        if persist:
            mcp_url = mcp_base_url or _resolve_mcp_url()
            result.persisted_artifact_id = await _persist(result, mcp_url)
        return result

    activity_snapshot = _build_activity_snapshot(artifacts)
    flagged_snapshot = _build_flagged_snapshot(flagged)
    inbox_snapshot = _build_inbox_snapshot(inbox)

    prompt = _DIGEST_PROMPT.format(
        window_hours=window_hours,
        activity_snapshot=activity_snapshot,
        flagged_snapshot=flagged_snapshot,
        inbox_snapshot=inbox_snapshot,
    )

    raw_response = await _call_llm(prompt)
    parsed: dict[str, Any] = _parse_llm_response(raw_response) if raw_response else {}

    # Merge LLM output into the result. Always preserve our deterministic
    # `top_categories` (count-based, not LLM-derived) but let the LLM
    # populate the `highlight` per category if it returned annotations.
    llm_categories = parsed.get("top_categories", [])
    if isinstance(llm_categories, list):
        highlight_by_domain = {
            c.get("domain"): str(c.get("highlight", ""))
            for c in llm_categories
            if isinstance(c, dict)
        }
        for cat in result.top_categories:
            cat["highlight"] = highlight_by_domain.get(cat["domain"], "")

    result.key_threads = _coerce_sections(parsed.get("key_threads"))
    result.urgent = _coerce_sections(parsed.get("urgent"))
    result.quality_alerts = _coerce_sections(parsed.get("quality_alerts"))
    action_items = parsed.get("action_items", [])
    if isinstance(action_items, list):
        result.action_items = [str(x).strip()[:100] for x in action_items if x][:10]

    if persist:
        mcp_url = mcp_base_url or _resolve_mcp_url()
        result.persisted_artifact_id = await _persist(result, mcp_url)

    return result


def _resolve_mcp_url() -> str:
    import os
    return os.getenv("CERID_MCP_INTERNAL_URL", "http://localhost:8888")
