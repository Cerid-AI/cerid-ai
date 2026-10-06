# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""One mail decision a review can print without a model or a ledger.

The function is pure. Triage still calls the model for bands that are
not skip. This module does not open a socket.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from config.settings import inbox_review_redact
from core.agents.inbox_actions import ThreadSignals, financial_marker, route_thread
from core.agents.inbox_filter import filter_verdict
from core.agents.inbox_rspamd import symbol_trace

_DOMAIN_RE = re.compile(r"@([A-Za-z0-9.-]+)")
SUBJECT_LIMIT = 70


def is_sensitive(*parts: str) -> bool:
    """True when a from, to, or subject must stay out of a review printout."""
    markers = inbox_review_redact()
    if not markers:
        return False
    blob = " ".join(parts).casefold()
    return any(marker in blob for marker in markers)


def domains(*parts: str) -> str:
    """Sender and recipient domains. Local parts stay out of the line."""
    found: list[str] = []
    for part in parts:
        for match in _DOMAIN_RE.findall(part):
            domain = match.casefold().strip(".")
            if domain and domain not in found:
                found.append(domain)
    return ",".join(found)


def _rspamd_fields(payload: Mapping[str, object] | None) -> tuple[str, float | None]:
    if not isinstance(payload, Mapping):
        return "", None
    action = str(payload.get("action") or "").strip()
    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return action, None
    return action, float(score)


def review_message(
    *,
    sender: str,
    subject: str,
    body: str,
    headers: Mapping[str, str] | None = None,
    rspamd: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """The decision triage would store before the model runs.

    ``headers`` uses the same keys as the triage header bag. ``rspamd``
    is an already parsed ``/checkv2`` body. This function does not fetch it.
    """
    excerpt = f"From: {sender}\nSubject: {subject}\n\n{body}"
    bag = {str(key): str(value) for key, value in (headers or {}).items()}
    verdict = filter_verdict(excerpt, subject=subject, headers=bag, rspamd=rspamd)
    route = route_thread(
        ThreadSignals(
            excerpt=excerpt,
            message_count=1,
            heuristic_category=verdict.category,
            heuristic_confidence=verdict.confidence,
            sticks=verdict.sticks,
        ),
    )
    action, score = _rspamd_fields(rspamd)
    return {
        "category": verdict.category,
        "action": route.action,
        "band": route.band,
        "confidence": verdict.confidence,
        "sticks": verdict.sticks,
        "reason": verdict.reason,
        "utility": route.utility,
        "financial_marker": financial_marker(excerpt),
        "rspamd_action": action,
        "score": score,
        "symbols": symbol_trace(rspamd),
    }


def review_line(
    mailbox: str,
    decision: Mapping[str, Any],
    *,
    sender: str,
    recipient: str,
    subject: str,
) -> str:
    """One review row. Addresses are reduced to domains."""
    score = decision.get("score")
    score_text = "" if not isinstance(score, (int, float)) or isinstance(score, bool) else f"{score:g}"
    raw_symbols = decision.get("symbols") or []
    parts: list[str] = []
    if isinstance(raw_symbols, list):
        for item in raw_symbols:
            if not isinstance(item, tuple):
                continue
            try:
                name, value = item
            except ValueError:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            parts.append(f"{name}:{value:g}")
    subject_text = " ".join(subject.split())[:SUBJECT_LIMIT]
    sticks = "true" if decision.get("sticks") else "false"
    return "\t".join((
        mailbox,
        str(decision.get("category") or ""),
        str(decision.get("action") or ""),
        f"sticks={sticks}",
        f"reason={decision.get('reason') or ''}",
        f"marker={decision.get('financial_marker') or ''}",
        f"rspamd={decision.get('rspamd_action') or ''}",
        f"score={score_text}",
        f"symbols={','.join(parts)}",
        f"domains={domains(sender, recipient)}",
        f"subject={subject_text}",
    ))


def render_record(record: Mapping[str, Any], *, mailbox: str = "") -> str | None:
    """One fixture row, or None when the row must not be printed."""
    sender = str(record.get("from") or "")
    recipient = str(record.get("to") or "")
    subject = str(record.get("subject") or "")
    if is_sensitive(sender, recipient, subject):
        return None
    raw_headers = record.get("headers")
    bag: dict[str, str] = {}
    if isinstance(raw_headers, Mapping):
        bag = {str(key): str(value) for key, value in raw_headers.items() if str(value).strip()}
    for key in ("in_reply_to", "references"):
        if key not in bag and str(record.get(key) or "").strip():
            bag[key] = str(record[key]).strip()
    raw_rspamd = record.get("rspamd")
    decision = review_message(
        sender=sender,
        subject=subject,
        body=str(record.get("body") or ""),
        headers=bag,
        rspamd=raw_rspamd if isinstance(raw_rspamd, Mapping) else None,
    )
    box = mailbox or str(record.get("mailbox") or "")
    return review_line(box, decision, sender=sender, recipient=recipient, subject=subject)
