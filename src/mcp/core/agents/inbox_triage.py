# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Inbox triage agent — Phase J Day 1.

Loads recent unread messages from Gmail, Outlook, and Apple Mail, groups
them by provider thread id, runs a local-model categorization per thread,
and writes a thread back into the KB only when the RAG route hook allows it.

Output contract per thread:
    {
      thread_id: str,
      participants: list[str],
      message_count: int,
      latest_at: str (ISO),
      category: one of "urgent" | "actionable" | "personal" |
                       "newsletter" | "promo" | "spam",
      summary: str (one-paragraph),
      suggested_action: str (e.g. "reply with apology", "no reply needed"),
      source: "gmail" | "outlook" | "apple_mail",
      artifact_id: str | None,   # set after /ingest/structured POST
    }

Idempotency:
  Each stored thread uses source_id = "inbox_triage:<source>:<thread_id>".
  A financial card uses the same id with a ":card" suffix. Utility "none"
  stores nothing. A subject-only grouping key is never written.
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

from core.agents.inbox_actions import (
    CATEGORY_ACTION,
    CLASSIFICATION_LADDER,
    MODEL_ACTIONS,
    PIN_CONFIDENCE,
    TIER_CONFIDENCE,
    UTILITIES,
    ThreadSignals,
    clamp_action,
    climb,
    draft_instruction,
    draft_problems,
    escalate,
    financial_marker,
    load_inbox_skill,
    parse_draft_text,
    route_thread,
    utility_for,
)
from core.agents.inbox_filter import (
    apply_verdict,
    filter_verdict,
    reconstructed_message,
    signal_note,
)
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
_rag_route: Any = None
_memory: Any = None


def set_inbox_registry(registry: _InboxRegistryProtocol) -> None:
    """Wire the app DataSourceRegistry in at startup (the DI boundary)."""
    global _registry
    _registry = registry


def get_inbox_registry() -> _InboxRegistryProtocol | None:
    return _registry


def set_inbox_rag_route(route: Any) -> None:
    """Wire app.inbox.hooks.rag_route_hook. Unwired triage does not ingest."""
    global _rag_route
    _rag_route = route


def set_inbox_memory(lookup: Any) -> None:
    """Wire app.inbox.learn.lookup_signals. Unwired triage does not read memory."""
    global _memory
    _memory = lookup


_rspamd_scan: Any = None


def set_inbox_rspamd(scan: Any) -> None:
    """Inject a scan coroutine. None uses the loopback client when configured."""
    global _rspamd_scan
    _rspamd_scan = scan


# Annotated-against app types; concrete classes arrive via the injected registry.
DataSource = Any
DataSourceResult = Any


# ── shape ─────────────────────────────────────────────────────────────

CATEGORIES = ("urgent", "actionable", "personal", "newsletter", "promo", "spam")

_TRIAGE_SOURCES = ("gmail", "outlook", "apple_mail")

_ACTION_PHRASE = {
    "keep": "review",
    "archive": "archive",
    "mark_read": "mark read",
    "draft": "draft",
}

_AMOUNT_RE = re.compile(
    r"(?P<cur>USD|EUR|GBP|\$|€|£)\s*(?P<amt>\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_FROM_RE = re.compile(r"^From:\s*(.+)$", re.MULTILINE)
_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_OBSERVATION_KEYS = ("mailbox", "labels", "categories", "flag", "folder")
_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}


@dataclass
class TriagedThread:
    thread_id: str
    source: str  # "gmail", "outlook", or "apple_mail"
    participants: list[str]
    subject: str
    message_count: int
    latest_at: str
    category: str  # one of CATEGORIES
    summary: str
    suggested_action: str
    artifact_id: str | None = None
    finance_artifact_id: str | None = None
    utility: str = "none"
    action: str = "keep"
    confidence: float = 0.0
    writable: bool = False
    message_ids: list[str] = field(default_factory=list)
    account: str = ""
    mailbox: str = ""
    draft_body: str = ""
    band: str = ""
    model: str = ""
    classification_reason: str = ""
    sender: str = ""
    observation: dict[str, str] = field(default_factory=dict)


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

_MAX_EXCERPT_CHARS = 2000


def _triage_prompt(excerpt: str) -> str:
    """The skill file is the instruction. The excerpt below it is data."""
    return f"{load_inbox_skill()}\n\nfolder_sort: false\n\nThread:\n{excerpt}"


def _category_repair_prompt(excerpt: str) -> str:
    """Ask for the category enum only.

    The full skill also describes a financial card. A small model sometimes
    returns that card and no category. This prompt does not mention the card.
    """
    names = " | ".join(CATEGORIES)
    return (
        "Return one JSON object and nothing else. "
        f"category is one of: {names}. "
        "confidence is a number from 0 to 1. "
        "No other keys.\n\n"
        f"Thread:\n{excerpt}"
    )


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


def _profile() -> str:
    import config

    return getattr(config, "CERID_ENVIRONMENT_PROFILE", "") or "hybrid"


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

def _provider_key(result: DataSourceResult) -> str | None:
    """A mailbox id. A normalized subject is not one."""
    meta = getattr(result, "metadata", None) or {}
    thread_id = str(meta.get("provider_thread_id") or "").strip()
    message_id = str(meta.get("provider_message_id") or "").strip()
    if thread_id:
        return thread_id
    if message_id:
        return message_id
    return None


def _subject_key(result: DataSourceResult) -> str:
    title = result.title or ""
    norm = re.sub(r"^\s*(re|fwd|fw|aw|tr)\s*:\s*", "", title, flags=re.IGNORECASE).strip()
    norm = re.sub(r"\s+", " ", norm).lower()
    return norm or "(no subject)"


def _extract_thread_id(result: DataSourceResult) -> str:
    """Group by provider thread id when the row has one.

    Subject grouping remains for rows that never carried an id. Those
    groups are not writable: a later apply must not target them.
    """
    return _provider_key(result) or _subject_key(result)


def _address_in(text: str) -> str:
    found = _ADDR_RE.search(text or "")
    return found.group(0).casefold() if found else ""


def _sender_address(message: DataSourceResult) -> str:
    found = _address_in(str(getattr(message, "source_name", "") or ""))
    if found:
        return found
    match = _FROM_RE.search(str(getattr(message, "content", "") or ""))
    if match:
        return _address_in(match.group(1))
    return ""


def _apply_fields(thread: TriagedThread, messages: list[DataSourceResult]) -> None:
    """Copy provider ids onto the thread. A subject line is not an id."""
    ids: list[str] = []
    account = ""
    mailbox = ""
    observation: dict[str, str] = {}
    sender = ""
    for msg in messages:
        meta = getattr(msg, "metadata", None) or {}
        if not isinstance(meta, dict):
            continue
        message_id = str(meta.get("provider_message_id") or "").strip()
        if message_id and message_id not in ids:
            ids.append(message_id)
        if not account:
            account = str(meta.get("account") or "").strip()
        if not mailbox:
            mailbox = str(meta.get("mailbox") or "").strip()
        for key in _OBSERVATION_KEYS:
            if key not in meta:
                continue
            value = meta[key]
            if isinstance(value, list):
                observation[key] = ",".join(str(item).strip() for item in value if str(item).strip())
            elif value is not None:
                observation[key] = str(value).strip()
        found = _sender_address(msg)
        if found:
            sender = found
    thread.message_ids = ids
    thread.account = account
    thread.mailbox = mailbox
    thread.observation = observation
    thread.sender = sender


def _thread_writable(messages: list[DataSourceResult]) -> bool:
    return bool(messages) and all(_provider_key(m) for m in messages)


def _group_by_thread(results: list[DataSourceResult], source_name: str) -> dict[str, list[DataSourceResult]]:
    del source_name
    threads: dict[str, list[DataSourceResult]] = defaultdict(list)
    for r in results:
        threads[_extract_thread_id(r)].append(r)
    return threads


# ── LLM categorization ────────────────────────────────────────────────

def _message_dicts(messages: list[DataSourceResult]) -> list[dict[str, Any]]:
    return [
        {
            "from": m.source_name or "unknown",
            "subject": m.title or "",
            "body": m.content or "",
        }
        for m in messages
    ]


def _draft_prompt(excerpt: str) -> str:
    """Separate from the classification skill so that schema stays closed."""
    return f"{draft_instruction()}\n\nMessage:\n{excerpt}"


async def _call_model(
    excerpt: str,
    stage: str,
    *,
    prompt: str | None = None,
    max_tokens: int = 300,
) -> Any:
    from core.utils.internal_llm import call_internal_llm

    content = _triage_prompt(excerpt) if prompt is None else prompt
    return await call_internal_llm(
        [{"role": "user", "content": content}],
        stage=stage,
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=max_tokens,
    )


def _with_band(parsed: dict[str, Any], *, band: str, model: str) -> dict[str, Any]:
    parsed["band"] = band
    parsed["model"] = model
    return parsed


def _numeric_confidence(parsed: dict[str, Any]) -> float:
    raw = parsed.get("confidence")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    return float(raw)


async def _attach_draft(
    parsed: dict[str, Any],
    excerpt: str,
    *,
    route_band: str,
) -> dict[str, Any]:
    """Local reply first. Cloud only after the checklist fails, and never local-only.

    A passing local draft is kept. A failing one is dropped. Skip-band
    callers must not reach this function.
    """
    if parsed.get("action") != "draft":
        return parsed
    local_body = ""
    local_failed = False
    try:
        response = await _call_model(
            excerpt,
            "inbox_triage_review",
            prompt=_draft_prompt(excerpt),
            max_tokens=400,
        )
        local_body = parse_draft_text(response)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("inbox_triage.draft", exc)
        local_failed = True
    if not local_failed and not draft_problems(local_body, excerpt):
        parsed["draft_body"] = local_body
        local_band = route_band if route_band in ("local-small", "local-chat") else "local-chat"
        parsed["band"] = local_band
        parsed["model"] = "inbox_triage_review"
        return parsed
    band = escalate(
        route_band,
        _profile(),
        local_failed=local_failed,
        confidence=_numeric_confidence(parsed),
        is_draft=True,
        draft_failed=True,
    )
    parsed.pop("draft_body", None)
    if band != "cloud":
        parsed["band"] = "needs_review"
        return parsed
    try:
        response = await _call_model(
            excerpt,
            "inbox_triage_draft",
            prompt=_draft_prompt(excerpt),
            max_tokens=400,
        )
        cloud_body = parse_draft_text(response)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("inbox_triage.draft.cloud", exc)
        parsed["band"] = "needs_review"
        return parsed
    if draft_problems(cloud_body, excerpt):
        parsed["band"] = "needs_review"
        return parsed
    parsed["draft_body"] = cloud_body
    parsed["band"] = "cloud"
    parsed["model"] = "inbox_triage_draft"
    return parsed


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _memory_signals(source: str, sender: str, subject: str, list_id: str) -> dict[str, Any]:
    lookup = _memory
    if lookup is None or not sender:
        return {}
    try:
        found = lookup(source, sender, subject, list_id)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("inbox_triage.memory", exc)
        return {}
    return found if isinstance(found, dict) else {}


_HEADER_KEYS = (
    "list_id",
    "list_unsubscribe",
    "authentication_results",
    "spam_flag",
    "spam_status",
    "labels",
    "in_reply_to",
    "references",
    "rfc_message_id",
    "to",
    "date",
)


def _header_bag(messages: list[DataSourceResult]) -> dict[str, str]:
    bag: dict[str, str] = {}
    for message in messages:
        meta = getattr(message, "metadata", None) or {}
        if not isinstance(meta, dict):
            continue
        for key in _HEADER_KEYS:
            if key in bag:
                continue
            value = str(meta.get(key) or "").strip()
            if value:
                bag[key] = value
    return bag


def _pinned(remembered: dict[str, Any]) -> bool:
    if (
        remembered.get("memory_action") in MODEL_ACTIONS
        and _as_float(remembered.get("memory_confidence")) >= PIN_CONFIDENCE
    ):
        return True
    return bool(
        remembered.get("rule_action") in MODEL_ACTIONS
        and _as_float(remembered.get("rule_confidence")) >= PIN_CONFIDENCE
    )


async def _scan_rspamd(
    *,
    sender: str,
    subject: str,
    message_id: str,
    body: str,
    to: str = "",
    date: str = "",
) -> dict[str, Any] | None:
    from core.agents.inbox_rspamd import SCAN_TIMEOUT_S, scan_if_configured

    scan = _rspamd_scan if _rspamd_scan is not None else scan_if_configured
    raw = reconstructed_message(
        sender=sender,
        subject=subject,
        body=body,
        message_id=message_id,
        to=to,
        date=date,
    )
    try:
        found = await asyncio.wait_for(scan(raw), timeout=SCAN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("inbox_triage.rspamd", exc)
        return None
    return found if isinstance(found, dict) else None


def _skipped(route: Any, thread_id: str, subject: str) -> dict[str, Any]:
    action = route.action if route.action in ("keep", "archive", "mark_read", "draft") else "keep"
    summary = (subject or thread_id)[:200]
    return {
        "category": route.category,
        "summary": summary or thread_id[:200],
        "suggested_action": _ACTION_PHRASE.get(action, "review"),
        "action": action,
        "utility": route.utility,
        "confidence": route.confidence,
        "band": "skip",
        "model": "",
    }


_BAND_STAGE = dict(CLASSIFICATION_LADDER)


async def _categorize_thread(
    thread_id: str,
    messages: list[DataSourceResult],
    *,
    source: str = "",
) -> dict[str, Any]:
    """Climb while the rung in hand is short of the tier confidence.

    A pin, a rule, or a sticking verdict skips the model. Otherwise the
    small local stage runs. A failed small call, including a reply that
    names no category, is tried once more on that same stage before the
    climb. A second miss gets one category-only repair on that stage and
    skips the heavy rung. A short but valid answer still climbs to the
    heavy stage, then the frontier stage. local-only stops before the
    frontier stage. A final action of ``draft`` then writes a local reply,
    and calls ``inbox_triage_draft`` only when that reply fails the
    checklist and the profile allows cloud.
    """
    excerpt = _build_thread_excerpt(_message_dicts(messages))
    subject = str(getattr(messages[0], "title", "") or "") if messages else ""
    sender = ""
    list_id = ""
    rfc_message_id = ""
    recipient = ""
    sent_at = ""
    headers = _header_bag(messages)
    for message in messages:
        if not sender:
            sender = _sender_address(message)
        meta = getattr(message, "metadata", None) or {}
        if not isinstance(meta, dict):
            continue
        if not list_id and str(meta.get("list_id") or "").strip():
            list_id = str(meta["list_id"]).strip()
        if not rfc_message_id and str(meta.get("rfc_message_id") or "").strip():
            rfc_message_id = str(meta["rfc_message_id"]).strip()
        if not recipient and str(meta.get("to") or "").strip():
            recipient = str(meta["to"]).strip()
        if not sent_at and str(meta.get("date") or "").strip():
            sent_at = str(meta["date"]).strip()
    remembered = _memory_signals(source, sender, subject, list_id)
    rspamd_payload = None if _pinned(remembered) else await _scan_rspamd(
        sender=sender,
        subject=subject,
        message_id=rfc_message_id,
        body=excerpt,
        to=recipient,
        date=sent_at,
    )
    verdict = filter_verdict(excerpt, subject=subject, headers=headers, rspamd=rspamd_payload)
    shown = excerpt + signal_note(headers, rspamd_payload)
    route = route_thread(
        ThreadSignals(
            excerpt=excerpt,
            message_count=len(messages),
            heuristic_category=verdict.category,
            heuristic_confidence=verdict.confidence,
            memory_action=remembered.get("memory_action"),
            memory_category=remembered.get("memory_category"),
            memory_confidence=_as_float(remembered.get("memory_confidence")),
            rule_action=remembered.get("rule_action"),
            rule_category=remembered.get("rule_category"),
            rule_confidence=_as_float(remembered.get("rule_confidence")),
            sticks=verdict.sticks,
        ),
    )
    if route.band == "skip":
        skipped = _skipped(route, thread_id, subject)
        if route.rationale == "deterministic" and verdict.reason:
            skipped["classification_reason"] = verdict.reason
        return skipped

    async def _finish(parsed: dict[str, Any], *, band: str) -> dict[str, Any]:
        adjusted = apply_verdict(parsed, verdict, excerpt)
        if verdict.reason:
            adjusted["classification_reason"] = verdict.reason
        return await _attach_draft(adjusted, excerpt, route_band=band)

    async def _read_rung(stage: str) -> tuple[bool, dict[str, Any] | None, bool]:
        try:
            response = await _call_model(shown, stage)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(f"inbox_triage.llm.{stage}", exc)
            return True, None, False
        reply = _model_reply(response)
        if reply is None:
            return True, None, True
        return False, _sanitize_categorization(
            reply, messages, thread_id, excerpt=excerpt,
        ), False

    async def _repair_category() -> tuple[bool, dict[str, Any] | None]:
        try:
            response = await _call_model(
                shown,
                CLASSIFICATION_LADDER[0][1],
                prompt=_category_repair_prompt(shown),
                max_tokens=120,
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("inbox_triage.llm.category_repair", exc)
            return True, None
        reply = _model_reply(response)
        if reply is None:
            return True, None
        return False, _sanitize_categorization(
            reply, messages, thread_id, excerpt=excerpt,
        )

    band = route.band
    last_parsed: dict[str, Any] | None = None
    last_stage = ""
    while band in _BAND_STAGE:
        stage = _BAND_STAGE[band]
        failed, parsed, schema_miss = await _read_rung(stage)
        # The small slot is one model. A schema miss gets one more draw
        # there before a heavier rung, or the frontier, is spent.
        if failed and band == CLASSIFICATION_LADDER[0][0]:
            failed, parsed, schema_miss = await _read_rung(stage)
        # A card-shaped reply names no category. The heavy rung is the
        # same local weights, so ask once for the category and skip it.
        repaired = False
        if failed and schema_miss and band == CLASSIFICATION_LADDER[0][0]:
            failed, parsed = await _repair_category()
            repaired = True
        if parsed is not None:
            last_parsed = parsed
            last_stage = stage
        confidence = _numeric_confidence(parsed or {})
        if repaired and not failed and confidence >= TIER_CONFIDENCE:
            decided = parsed if parsed is not None else {}
            return await _finish(_with_band(decided, band=band, model=stage), band=band)
        nxt = climb(
            CLASSIFICATION_LADDER[1][0] if repaired else band,
            _profile(),
            failed=failed,
            confidence=confidence,
        )
        if nxt == band:
            decided = parsed if parsed is not None else {}
            return await _finish(_with_band(decided, band=band, model=stage), band=band)
        if nxt == "needs_review":
            held = last_parsed if last_parsed is not None else _heuristic_categorize(messages, thread_id)
            return await _finish(
                _with_band(
                    held,
                    band="needs_review",
                    model=last_stage if last_parsed is not None else "",
                ),
                band="needs_review",
            )
        band = nxt
    held = last_parsed if last_parsed is not None else _heuristic_categorize(messages, thread_id)
    return await _finish(_with_band(held, band="needs_review", model=last_stage), band="needs_review")


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.S)
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", flags=re.S)


def _load_json_object(raw: Any) -> dict[str, Any] | None:
    """Dict, fenced JSON, or the first embedded object. Anything else is None."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    cleaned = _FENCE_RE.sub("", raw.strip())
    found: Any = None
    try:
        found = json.loads(cleaned)
    except (ValueError, TypeError):
        found = None
    if not isinstance(found, dict):
        match = _JSON_OBJECT_RE.search(cleaned)
        if match:
            try:
                found = json.loads(match.group(0))
            except (ValueError, TypeError):
                found = None
    return found if isinstance(found, dict) else None


def _model_reply(raw: Any) -> dict[str, Any] | None:
    """A reply that names a real category. A miss climbs; it is not a decision."""
    found = _load_json_object(raw)
    if found is None:
        return None
    category = str(found.get("category", "")).lower().strip()
    if category not in CATEGORIES:
        return None
    return found


def _parse_triage_response(
    raw: Any,
    *,
    fallback_messages: list[DataSourceResult],
    thread_id: str,
    excerpt: str = "",
) -> dict[str, Any]:
    """Tolerant parser — accepts dict, code-fenced JSON, embedded JSON,
    or falls back to heuristic when nothing parses."""
    reply = _model_reply(raw)
    if reply is not None:
        return _sanitize_categorization(reply, fallback_messages, thread_id, excerpt=excerpt)
    loaded = _load_json_object(raw)
    if loaded is not None:
        return _sanitize_categorization(loaded, fallback_messages, thread_id, excerpt=excerpt)
    return _heuristic_categorize(fallback_messages, thread_id)


def _sanitize_categorization(
    parsed: dict[str, Any],
    fallback_messages: list[DataSourceResult],
    thread_id: str,
    *,
    excerpt: str = "",
) -> dict[str, Any]:
    """Clamp + validate the LLM output to the documented contract.

    ``suggested_action`` stays free text for the existing chat surface.
    ``action`` is the closed enum. A word outside that enum cannot change it.
    """
    category = str(parsed.get("category", "")).lower().strip()
    if category not in CATEGORIES:
        # LLM hallucinated a category not in our enum — heuristic
        return _heuristic_categorize(fallback_messages, thread_id)
    summary = str(parsed.get("summary", "")).strip()[:500] or thread_id
    suggested_action = str(parsed.get("suggested_action", "")).strip()[:200]
    raw_action = parsed.get("action")
    proposed = str(raw_action).strip() if isinstance(raw_action, str) else None
    action = clamp_action(proposed or None, category)
    if not suggested_action:
        suggested_action = _ACTION_PHRASE.get(action, "review")
    raw_utility = str(parsed.get("utility", "")).lower().strip()
    utility = raw_utility if raw_utility in UTILITIES else utility_for(excerpt, category)
    result: dict[str, Any] = {
        "category": category,
        "summary": summary,
        "suggested_action": suggested_action,
        "action": action,
        "utility": utility,
    }
    if parsed.get("confidence") is not None:
        try:
            result["confidence"] = float(parsed["confidence"])
        except (TypeError, ValueError):
            pass
    return result


def _heuristic_categorize(
    messages: list[DataSourceResult],
    thread_id: str,
) -> dict[str, Any]:
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
    ])
    subject = " ".join(m.title or "" for m in messages)
    verdict = filter_verdict(text, subject=subject, headers=_header_bag(messages))
    cat = verdict.category
    action = CATEGORY_ACTION.get(cat, "keep")
    return {
        "category": cat,
        "summary": thread_id[:200],
        "suggested_action": "review" if action == "keep" else "archive",
        "action": action,
        "utility": utility_for(text, cat),
        "confidence": verdict.confidence,
    }


# ── write-back to KB ──────────────────────────────────────────────────

def _financial_card(thread: TriagedThread, excerpt: str) -> str:
    """Payee, amount, currency, and date — only the ones the excerpt states."""
    lines: list[str] = []
    senders = [line.strip() for line in _FROM_RE.findall(excerpt)]
    addressed = [line for line in senders if "@" in line]
    payee = addressed[0] if addressed else (senders[-1] if senders else "")
    if not payee and thread.participants:
        payee = thread.participants[0]
    if payee:
        lines.append(f"Payee: {payee[:120]}")
    amount = _AMOUNT_RE.search(excerpt)
    if amount:
        lines.append(f"Amount: {amount.group('amt').replace(',', '')}")
        currency = _CURRENCY.get(amount.group("cur"), amount.group("cur").upper())
        lines.append(f"Currency: {currency}")
    found_date = _DATE_RE.search(excerpt)
    if found_date:
        lines.append(f"Date: {found_date.group(1)}")
    marker = financial_marker(excerpt)
    if marker:
        lines.append(f"Marker: {marker}")
    lines.append(f"Subject: {thread.subject[:200]}")
    lines.append(f"Provider thread: {thread.thread_id}")
    return "\n".join(lines)


def _string_meta(thread: TriagedThread, record_type: str) -> dict[str, str]:
    return {
        "source": "inbox_triage",
        "origin_source": thread.source,
        "category": thread.category,
        "utility": thread.utility,
        "action": thread.action,
        "summary": thread.summary,
        "suggested_action": thread.suggested_action,
        "thread_id": thread.thread_id,
        "subject": thread.subject,
        "latest_at": thread.latest_at,
        "message_count": str(thread.message_count),
        "record_type": record_type,
    }


def _posts_for(thread: TriagedThread, excerpt: str) -> list[dict[str, Any]]:
    """Artifacts the hook may accept. The raw body is not one of them."""
    if thread.utility == "none":
        return []
    base = f"inbox_triage:{thread.source}:{thread.thread_id}"
    if thread.utility == "financial":
        return [
            {
                "domain": "finance",
                "decision": {"utility": "financial", "payload_kind": "card"},
                "content": _financial_card(thread, excerpt),
                "source_id": f"{base}:card",
                "metadata": _string_meta(thread, "mail_financial_card"),
            },
            {
                "domain": "inbox",
                "decision": {"utility": "correspondence", "payload_kind": "excerpt"},
                "content": f"{thread.subject[:200]} — finance card",
                "source_id": base,
                "metadata": _string_meta(thread, "mail_finance_pointer"),
            },
        ]
    body = excerpt.strip()[:800]
    content = (
        f"# {thread.subject}\n\n"
        f"**Category:** {thread.category}\n"
        f"**Summary:** {thread.summary}\n"
        f"**Participants:** {', '.join(thread.participants) or '(none)'}\n\n"
        f"{body}"
    )
    return [
        {
            "domain": "inbox",
            "decision": {"utility": "correspondence", "payload_kind": "excerpt"},
            "content": content,
            "source_id": base,
            "metadata": _string_meta(thread, "mail_thread"),
        },
    ]


async def _post_ingest(mcp_base_url: str, item: dict[str, Any], domain: str) -> str | None:
    import httpx

    payload = {
        "content": item["content"],
        "domain": domain,
        "source_id": item["source_id"],
        "metadata": item["metadata"],
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
                resp.status_code, item["source_id"],
            )
            return None
        body = resp.json()
        return body.get("artifact_id") or body.get("id")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("inbox_triage.persist", exc)
        return None


async def _persist_to_kb(thread: TriagedThread, mcp_base_url: str, excerpt: str) -> None:
    """Ingest only what rag_route_hook accepts, and only for a provider id."""
    if not thread.writable or not thread.thread_id:
        return
    gate = _rag_route
    if gate is None:
        return
    for item in _posts_for(thread, excerpt):
        routed = gate(item["decision"])
        if routed is None or not routed.ok or not routed.domain:
            continue
        if routed.domain != item["domain"]:
            continue
        artifact_id = await _post_ingest(mcp_base_url, item, routed.domain)
        if item["domain"] == "finance":
            thread.finance_artifact_id = artifact_id
        elif thread.artifact_id is None:
            thread.artifact_id = artifact_id


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
        max_results_per_source: cap per Gmail/Outlook/Apple fetch (LLM cost
            scales with this).
        query: source-specific filter string. Default fetches recent
            unread (Gmail honors it natively; Outlook tolerates as-is;
            Apple Mail reads ``since`` for the last day unless ``query``
            itself is an ISO-8601 cursor).
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
    for source_name in _TRIAGE_SOURCES:
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
            _categorize_thread(thread_id, msgs, source=source.name)
            for thread_id, msgs in threads.items()
        ]
        categorizations = await asyncio.gather(*triage_tasks)

        for (thread_id, msgs), cat in zip(threads.items(), categorizations, strict=True):
            participants = sorted({m.source_name for m in msgs if m.source_name})
            latest_at = max(
                (m.confidence for m in msgs),  # crude: confidence != date but msgs don't expose date
                default=0.0,
            )
            excerpt = _build_thread_excerpt(_message_dicts(msgs))
            model_confidence = cat.get("confidence")
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
                utility=str(cat.get("utility") or "none"),
                action=str(cat.get("action") or "keep"),
                confidence=float(model_confidence) if isinstance(model_confidence, (int, float)) else 0.0,
                writable=_thread_writable(msgs),
                draft_body=str(cat.get("draft_body") or ""),
                band=str(cat.get("band") or ""),
                model=str(cat.get("model") or ""),
                classification_reason=str(cat.get("classification_reason") or ""),
            )
            _apply_fields(thread, msgs)
            if persist and mcp_base_url:
                await _persist_to_kb(thread, mcp_base_url, excerpt)
            by_category[thread.category] += 1
            result.threads.append(thread)

    result.by_category = dict(by_category)
    return result
