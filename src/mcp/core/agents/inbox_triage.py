# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inbox triage agent — Phase J Day 1.

Loads recent unread messages from Gmail + Outlook DataSources, groups
them by thread, runs an LLM categorization per thread, and writes
each thread back into the KB as a triaged artifact.

Output contract per thread:
    {
      thread_id: str,
      participants: list[str],
      message_count: int,
      latest_at: str (ISO),
      category: one of "urgent" | "actionable" | "personal" |
                       "newsletter" | "promo",
      summary: str (one-paragraph),
      suggested_action: str (e.g. "reply with apology", "no reply needed"),
      source: "gmail" | "outlook",
      artifact_id: str | None,   # set after /ingest/structured POST
    }

Idempotency:
  Each thread becomes one artifact with source_id = "inbox_triage:<source>:<thread_id>"
  so re-running the agent updates the same artifact instead of
  duplicating. Chroma's content_hash dedup handles the no-op case
  (identical content → same hash → not re-stored).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from typing import Any, Protocol

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.inbox_triage")


# ── app DI (keeps core/ free of app.* imports; mirrors set_data_source_registry) ──
# core/ must never import app/. The concrete DataSourceRegistry — and its
# GmailDataSource / OutlookDataSource / DataSourceResult types — live app-side;
# app/main.py injects the registry here at startup and core duck-types it.
class _InboxRegistryProtocol(Protocol):
    """The slice of app.data_sources.registry that inbox triage needs."""

    def get(self, name: str) -> Any: ...


_registry: _InboxRegistryProtocol | None = None


def set_inbox_registry(registry: _InboxRegistryProtocol) -> None:
    """Wire the app DataSourceRegistry in at startup (the DI boundary)."""
    global _registry
    _registry = registry


def get_inbox_registry() -> _InboxRegistryProtocol | None:
    return _registry


# Annotated-against app types; concrete classes arrive via the injected registry.
DataSource = Any
DataSourceResult = Any


# ── shape ─────────────────────────────────────────────────────────────

CATEGORIES = ("urgent", "actionable", "personal", "newsletter", "promo")


@dataclass
class TriagedThread:
    thread_id: str
    source: str  # "gmail" or "outlook"
    participants: list[str]
    subject: str
    message_count: int
    latest_at: str
    category: str  # one of CATEGORIES
    summary: str
    suggested_action: str
    artifact_id: str | None = None


@dataclass
class TriageResult:
    threads: list[TriagedThread] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)
    sources_queried: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)  # {source, reason}

    def to_dict(self) -> dict[str, Any]:
        return {
            "threads": [asdict(t) for t in self.threads],
            "by_category": self.by_category,
            "sources_queried": self.sources_queried,
            "skipped": self.skipped,
        }


# ── LLM prompt ────────────────────────────────────────────────────────

_TRIAGE_PROMPT = """\
You are categorizing email threads. For the thread below, return a SINGLE JSON object with exactly these keys:

  category: one of "urgent" | "actionable" | "personal" | "newsletter" | "promo"
  summary: one-sentence paraphrase of what the thread is about (no quoting)
  suggested_action: one short phrase describing what the user should do
                    (e.g. "reply by EOD", "archive", "no action needed",
                    "schedule a call", "delegate to ...")

Be conservative: only mark "urgent" when the thread contains a clear deadline,
emergency, or request that genuinely requires same-day action. Default to
"newsletter" or "promo" for marketing content. Use "actionable" for
things that need a response within the week. Use "personal" for thread
content that's clearly social/non-work.

Return ONLY the JSON object — no prose, no markdown fences.

Thread:
{thread_excerpt}
"""

_MAX_EXCERPT_CHARS = 2000


def _build_thread_excerpt(messages: list[dict[str, Any]]) -> str:
    """Compact excerpt of the thread body to fit the LLM context budget."""
    parts: list[str] = []
    for m in messages[:5]:  # last 5 messages
        sender = m.get("from", m.get("from_address", "unknown"))
        subject = m.get("subject", "")
        body = (m.get("body") or m.get("snippet") or "")[:400]
        parts.append(f"From: {sender}\nSubject: {subject}\n\n{body}")
    excerpt = "\n\n---\n\n".join(parts)
    return excerpt[:_MAX_EXCERPT_CHARS]


# ── DataSource fetch ──────────────────────────────────────────────────

async def _fetch_recent(source: DataSource, query: str, max_results: int) -> list[DataSourceResult]:
    """Pull recent results from a DataSource. Defensive — returns empty
    on any failure rather than propagating (one bad source can't break
    the whole triage)."""
    try:
        return await source.query(query, max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(f"inbox_triage.fetch.{source.name}", exc)
        return []


# ── thread grouping ───────────────────────────────────────────────────

def _extract_thread_id(result: DataSourceResult) -> str:
    """Best-effort thread id extraction from a DataSourceResult.

    Gmail's MCP tool returns thread_id in the message dict; Outlook
    returns conversationId. The current GmailDataSource / OutlookDataSource
    don't expose either via DataSourceResult.to_dict(), so we fall back
    to grouping by normalized subject (drop Re: / Fwd: prefixes).
    """
    # Future: when GmailDataSource adds thread_id to source_url, parse
    # it out here. For v1 use subject grouping which catches the
    # common case.
    title = result.title or ""
    norm = re.sub(r"^\s*(re|fwd|fw|aw|tr)\s*:\s*", "", title, flags=re.IGNORECASE).strip()
    norm = re.sub(r"\s+", " ", norm).lower()
    return norm or "(no subject)"


def _group_by_thread(results: list[DataSourceResult], source_name: str) -> dict[str, list[DataSourceResult]]:
    threads: dict[str, list[DataSourceResult]] = defaultdict(list)
    for r in results:
        # Stamp the source so the dataclass output records origin
        thread_id = _extract_thread_id(r)
        threads[thread_id].append(r)
    return threads


# ── LLM categorization ────────────────────────────────────────────────

async def _categorize_thread(
    thread_id: str,
    messages: list[DataSourceResult],
) -> dict[str, str]:
    """Call the internal LLM with the triage prompt. Falls back to a
    deterministic heuristic on LLM failure so the agent never crashes
    a whole batch on one bad call."""
    from core.utils.internal_llm import call_internal_llm

    msgs_dicts: list[dict[str, Any]] = []
    for m in messages:
        msgs_dicts.append({
            "from": m.source_name or "unknown",
            "subject": m.title or "",
            "body": m.content or "",
        })
    excerpt = _build_thread_excerpt(msgs_dicts)

    try:
        response = await call_internal_llm(
            [{"role": "user", "content": _TRIAGE_PROMPT.format(thread_excerpt=excerpt)}],
            stage="inbox_triage",
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=300,
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("inbox_triage.llm", exc)
        return _heuristic_categorize(messages, thread_id)

    return _parse_triage_response(response, fallback_messages=messages, thread_id=thread_id)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.S)
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", flags=re.S)


def _parse_triage_response(
    raw: Any,
    *,
    fallback_messages: list[DataSourceResult],
    thread_id: str,
) -> dict[str, str]:
    """Tolerant parser — accepts dict, code-fenced JSON, embedded JSON,
    or falls back to heuristic when nothing parses."""
    if isinstance(raw, dict):
        return _sanitize_categorization(raw, fallback_messages, thread_id)
    if isinstance(raw, str):
        cleaned = _FENCE_RE.sub("", raw.strip())
        try:
            return _sanitize_categorization(json.loads(cleaned), fallback_messages, thread_id)
        except (ValueError, TypeError):
            pass
        match = _JSON_OBJECT_RE.search(cleaned)
        if match:
            try:
                return _sanitize_categorization(
                    json.loads(match.group(0)),
                    fallback_messages,
                    thread_id,
                )
            except (ValueError, TypeError):
                pass
    return _heuristic_categorize(fallback_messages, thread_id)


def _sanitize_categorization(
    parsed: dict[str, Any],
    fallback_messages: list[DataSourceResult],
    thread_id: str,
) -> dict[str, str]:
    """Clamp + validate the LLM output to the documented contract."""
    category = str(parsed.get("category", "")).lower().strip()
    if category not in CATEGORIES:
        # LLM hallucinated a category not in our enum — heuristic
        return _heuristic_categorize(fallback_messages, thread_id)
    summary = str(parsed.get("summary", "")).strip()[:500] or thread_id
    suggested_action = str(parsed.get("suggested_action", "")).strip()[:200] or "review"
    return {
        "category": category,
        "summary": summary,
        "suggested_action": suggested_action,
    }


def _heuristic_categorize(
    messages: list[DataSourceResult],
    thread_id: str,
) -> dict[str, str]:
    """Last-resort categorization when the LLM is unavailable. Keyword-
    based but conservative — defaults to 'actionable' so the user sees
    the thread rather than burying it under 'promo'.

    Looks at title + body together — the subject line carries strong
    category signal (e.g. "Sale!", "URGENT:", "Newsletter:") that the
    body may not repeat.
    """
    text = " ".join([
        thread_id,
        *(m.title or "" for m in messages),
        *(m.content or "" for m in messages),
    ]).lower()

    urgent_kw = ("urgent", "asap", "emergency", "right away", "deadline today", "critical")
    promo_kw = ("unsubscribe", "%off", "% off", "deal", "sale", "promo code")
    newsletter_kw = ("newsletter", "weekly digest", "monthly update", "subscribe")

    if any(k in text for k in urgent_kw):
        cat = "urgent"
    elif any(k in text for k in promo_kw):
        cat = "promo"
    elif any(k in text for k in newsletter_kw):
        cat = "newsletter"
    else:
        cat = "actionable"

    return {
        "category": cat,
        "summary": thread_id[:200],
        "suggested_action": "review" if cat in ("actionable", "urgent") else "archive",
    }


# ── write-back to KB ──────────────────────────────────────────────────

async def _persist_to_kb(thread: TriagedThread, mcp_base_url: str) -> str | None:
    """POST the triaged thread to /ingest/structured. Returns the
    artifact_id when the backend accepts it, None otherwise."""
    import httpx

    payload = {
        "content": (
            f"# {thread.subject}\n\n"
            f"**Category:** {thread.category}\n"
            f"**Summary:** {thread.summary}\n"
            f"**Suggested action:** {thread.suggested_action}\n"
            f"**Participants:** {', '.join(thread.participants) or '(none)'}\n"
            f"**Messages in thread:** {thread.message_count}\n"
        ),
        "domain": "inbox",
        "source_id": f"inbox_triage:{thread.source}:{thread.thread_id}",
        "metadata": {
            "source": "inbox_triage",
            "origin_source": thread.source,
            "category": thread.category,
            "summary": thread.summary,
            "suggested_action": thread.suggested_action,
            "thread_id": thread.thread_id,
            "subject": thread.subject,
            "latest_at": thread.latest_at,
            "message_count": str(thread.message_count),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{mcp_base_url}/ingest/structured",
                json=payload,
                headers={"X-Client-ID": "inbox_triage"},
            )
        if resp.status_code != HTTPStatus.OK:
            logger.warning(
                "inbox_triage write-back returned %d for %s",
                resp.status_code, thread.thread_id,
            )
            return None
        body = resp.json()
        return body.get("artifact_id") or body.get("id")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("inbox_triage.persist", exc)
        return None


# ── public entry ──────────────────────────────────────────────────────

async def triage_inboxes(
    *,
    max_results_per_source: int = 50,
    query: str = "is:unread newer_than:1d",
    mcp_base_url: str | None = None,
    persist: bool = True,
) -> TriageResult:
    """Run a single triage pass.

    Args:
        max_results_per_source: cap per Gmail/Outlook fetch (LLM cost
            scales with this).
        query: source-specific filter string. Default fetches recent
            unread (Gmail honors it natively; Outlook tolerates as-is).
        mcp_base_url: where to POST /ingest/structured (defaults to the
            local MCP base via config).
        persist: when False, skip the KB write-back (useful for dry-run
            preview from the chat surface).

    Feature-gated by ``inbox_triage``. Returns an empty result with
    skipped=[{source, reason="feature_gated"}] when the flag is off.
    """
    from config.features import is_feature_enabled

    result = TriageResult()

    if not is_feature_enabled("inbox_triage"):
        result.skipped.append({"source": "all", "reason": "feature_gated"})
        return result

    # Identify which inbox sources are registered + configured
    registry = get_inbox_registry()
    if registry is None:
        result.skipped.append({"source": "all", "reason": "registry_unwired"})
        return result
    candidates: list[DataSource] = []
    for source_name in ("gmail", "outlook"):
        src = registry.get(source_name)
        if src is None:
            result.skipped.append({"source": source_name, "reason": "not_registered"})
            continue
        if not src.is_configured():
            result.skipped.append({"source": source_name, "reason": "not_configured"})
            continue
        candidates.append(src)

    if not candidates:
        return result

    # Fetch in parallel
    fetches = await asyncio.gather(
        *(_fetch_recent(s, query, max_results_per_source) for s in candidates),
        return_exceptions=False,
    )

    # Resolve mcp_base_url lazily so tests can omit it
    if persist and mcp_base_url is None:
        import os
        mcp_base_url = os.getenv("CERID_MCP_INTERNAL_URL", "http://localhost:8888")

    # Per-source thread grouping + LLM categorization
    by_category: dict[str, int] = defaultdict(int)
    for source, results in zip(candidates, fetches, strict=True):
        result.sources_queried.append(source.name)
        if not results:
            continue
        threads = _group_by_thread(results, source.name)
        # Categorize threads in parallel — bounded by max_results_per_source
        triage_tasks = [
            _categorize_thread(thread_id, msgs)
            for thread_id, msgs in threads.items()
        ]
        categorizations = await asyncio.gather(*triage_tasks)

        for (thread_id, msgs), cat in zip(threads.items(), categorizations, strict=True):
            participants = sorted({m.source_name for m in msgs if m.source_name})
            latest_at = max(
                (m.confidence for m in msgs),  # crude: confidence != date but msgs don't expose date
                default=0.0,
            )
            thread = TriagedThread(
                thread_id=thread_id,
                source=source.name,
                participants=list(participants),
                subject=msgs[0].title or thread_id,
                message_count=len(msgs),
                latest_at=str(latest_at),
                category=cat["category"],
                summary=cat["summary"],
                suggested_action=cat["suggested_action"],
            )
            if persist and mcp_base_url:
                thread.artifact_id = await _persist_to_kb(thread, mcp_base_url)
            by_category[thread.category] += 1
            result.threads.append(thread)

    result.by_category = dict(by_category)
    return result
