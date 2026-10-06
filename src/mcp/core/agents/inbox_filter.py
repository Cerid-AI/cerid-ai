# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Local spam and folder signals. No mailbox I/O and no network.

Phrase markers still run first. Provider headers and a parsed rspamd
reply can override them. A bill is not filed as spam on a score alone.
"""
from __future__ import annotations

import email.utils
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.agents.inbox_actions import (
    _NEWSLETTER_MARKERS,
    _SPAM_MARKERS,
    _URGENT_MARKERS,
    CATEGORY_ACTION,
    first_marker,
    heuristic_category,
    promo_marker,
    utility_for,
)

STRUCTURAL_CONFIDENCE = 0.9
PHRASE_CONFIDENCE = 0.9
LIST_CONFIDENCE = 0.86
REVIEW_CONFIDENCE = 0.55
SOCIAL_CONFIDENCE = 0.75
_BODY_CAP = 24000
_HEADER_CAP = 200

_PROMO_LABELS = frozenset({"category_promotions"})
_NEWS_LABELS = frozenset({"category_updates", "category_forums"})
_PERSONAL_LABELS = frozenset({"category_social", "category_personal"})
_REVIEW_ACTIONS = frozenset({"add header", "rewrite subject", "soft reject"})


@dataclass(frozen=True)
class FilterVerdict:
    """Category a structural signal will keep even when the model disagrees."""

    category: str
    confidence: float
    sticks: bool
    reason: str = ""


def _labels(value: str) -> set[str]:
    return {part.strip().casefold() for part in value.replace(";", ",").split(",") if part.strip()}


def _subject_urgent(subject: str) -> bool:
    category, _confidence = heuristic_category(subject)
    return category == "urgent"


def _bill(text: str) -> bool:
    return utility_for(text, "spam") == "financial"


def _spam_flag(headers: Mapping[str, str]) -> bool:
    flag = headers.get("spam_flag", "").strip().casefold()
    status = headers.get("spam_status", "").strip().casefold()
    return flag == "yes" or status.startswith("yes")


def _list_mail(headers: Mapping[str, str]) -> bool:
    return bool(headers.get("list_id", "").strip() or headers.get("list_unsubscribe", "").strip())


def dmarc_failed(headers: Mapping[str, str]) -> bool:
    """Any DMARC fail in the Authentication-Results. SPF fail alone is not this.

    The sources keep every copy of the header, and a sender can add a copy
    that says pass; a pass therefore never cancels a fail.
    """
    auth = headers.get("authentication_results", "").casefold()
    return "dmarc=fail" in auth


def rspamd_is_spam(payload: Mapping[str, object] | None) -> bool:
    """Reject, or a score at the daemon's required threshold."""
    if not payload or payload.get("is_skipped") is True:
        return False
    action = str(payload.get("action") or "").strip().casefold()
    if action == "reject":
        return True
    score = payload.get("score")
    required = payload.get("required_score")
    if isinstance(score, bool) or isinstance(required, bool):
        return False
    if isinstance(score, (int, float)) and isinstance(required, (int, float)):
        return float(score) >= float(required)
    return False


def rspamd_needs_review(payload: Mapping[str, object] | None) -> bool:
    """Add-header and greylist-adjacent actions. Not an archive by themselves."""
    if not payload or rspamd_is_spam(payload) or payload.get("is_skipped") is True:
        return False
    action = str(payload.get("action") or "").strip().casefold()
    return action in _REVIEW_ACTIONS


def _reply(headers: Mapping[str, str]) -> bool:
    return bool(headers.get("in_reply_to", "").strip() or headers.get("references", "").strip())


def filter_verdict(
    text: str,
    *,
    subject: str,
    headers: Mapping[str, str] | None = None,
    rspamd: Mapping[str, object] | None = None,
) -> FilterVerdict:
    """Combine phrase, provider, and rspamd signals. The first match wins.

    Urgent markers and the sale marker are read from the subject. Spam,
    the other promo markers, newsletter, and bills still see the body. A
    phrase promo or newsletter sticks. A reply is only a personal hint.
    """
    bag = {str(key): str(value) for key, value in (headers or {}).items()}
    category, confidence = heuristic_category(text, include_urgent=False)
    promo = promo_marker(text, subject)
    if promo and category in ("actionable", "newsletter"):
        category, confidence = "promo", PHRASE_CONFIDENCE
    urgent_subject = _subject_urgent(subject)
    if category == "spam":
        return FilterVerdict("spam", confidence, True, f"phrase:{first_marker(text, _SPAM_MARKERS)}")
    labels = _labels(bag.get("labels", ""))
    if _spam_flag(bag) or "spam" in labels:
        return FilterVerdict("spam", STRUCTURAL_CONFIDENCE, True, "header:spam")
    if rspamd_is_spam(rspamd) and not _bill(text):
        return FilterVerdict("spam", STRUCTURAL_CONFIDENCE, True, "rspamd:reject")

    if urgent_subject:
        category = "urgent"
        confidence = 0.9
    if labels & _PROMO_LABELS and not urgent_subject:
        return FilterVerdict("promo", STRUCTURAL_CONFIDENCE, True, "gmail:promotions")
    if _list_mail(bag) and not urgent_subject:
        if category == "promo":
            return FilterVerdict("promo", confidence, True, f"phrase:{promo}")
        reason = "list-id" if bag.get("list_id", "").strip() else "list-unsubscribe"
        return FilterVerdict("newsletter", LIST_CONFIDENCE, True, reason)
    if labels & _NEWS_LABELS and not urgent_subject and category != "promo":
        return FilterVerdict("newsletter", LIST_CONFIDENCE, True, "gmail:updates")
    if category == "promo":
        return FilterVerdict("promo", confidence, True, f"phrase:{promo}")
    if category == "newsletter":
        return FilterVerdict(
            "newsletter", confidence, True, f"phrase:{first_marker(text, _NEWSLETTER_MARKERS)}",
        )
    if rspamd_needs_review(rspamd) and category == "actionable":
        return FilterVerdict("actionable", REVIEW_CONFIDENCE, False, "rspamd:review")
    if dmarc_failed(bag) and category == "urgent":
        return FilterVerdict("urgent", REVIEW_CONFIDENCE, False, "dmarc:fail")
    if labels & _PERSONAL_LABELS and category == "actionable":
        return FilterVerdict("personal", SOCIAL_CONFIDENCE, False, "gmail:social")
    if category == "actionable" and _reply(bag) and not _list_mail(bag):
        return FilterVerdict("personal", SOCIAL_CONFIDENCE, False, "header:in-reply-to")
    if category == "urgent":
        return FilterVerdict("urgent", confidence, False, f"subject:{first_marker(subject, _URGENT_MARKERS)}")
    return FilterVerdict(category, confidence, False)


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def apply_verdict(parsed: dict[str, Any], verdict: FilterVerdict, text: str) -> dict[str, Any]:
    """Keep a structural category when the model disagrees. Pins never call this."""
    if not verdict.sticks:
        return parsed
    category = verdict.category
    action = CATEGORY_ACTION.get(category, "keep")
    if parsed.get("category") == category and parsed.get("action") == action:
        return parsed
    out = dict(parsed)
    out["category"] = category
    out["action"] = action
    out["utility"] = utility_for(text, category)
    out["confidence"] = max(verdict.confidence, _confidence(parsed.get("confidence")))
    out["suggested_action"] = "archive" if action == "archive" else "review"
    return out


def _header_value(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()[:_HEADER_CAP]


def reconstructed_message(
    *,
    sender: str,
    subject: str,
    body: str,
    message_id: str = "",
    to: str = "",
    date: str = "",
) -> bytes:
    """A plain RFC822 document for a local rspamd scan.

    Authentication headers are omitted so a rebuilt message is not scored
    as a forged signature. An empty To is omitted. A fake undisclosed
    recipient made every scan share the same structural penalty.
    """
    token = _header_value(message_id)
    if not token:
        token = "cerid-local@localhost"
    if not (token.startswith("<") and token.endswith(">")):
        token = f"<{token.strip('<>')}>"
    lines = [f"From: {_header_value(sender) or 'unknown@localhost'}"]
    recipient = _header_value(to)
    if recipient:
        lines.append(f"To: {recipient}")
    lines.extend((
        f"Subject: {_header_value(subject) or '(no subject)'}",
        f"Date: {_header_value(date) or email.utils.formatdate(localtime=False)}",
        f"Message-ID: {token}",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "",
        body[:_BODY_CAP],
    ))
    return "\n".join(lines).encode("utf-8", errors="replace")


_STRUCTURAL_SYMBOLS = frozenset({
    "R_UNDISC_RCPT",
    "MID_RHS_NOT_FQDN",
    "ONCE_RECEIVED",
    "MISSING_MID",
    "MIME_GOOD",
})


def _structural_only(rspamd: Mapping[str, object] | None) -> bool:
    from core.agents.inbox_rspamd import symbol_trace

    traced = symbol_trace(rspamd)
    return bool(traced) and all(name in _STRUCTURAL_SYMBOLS for name, _score in traced)


def signal_note(headers: Mapping[str, str], rspamd: Mapping[str, object] | None) -> str:
    """Short facts for the model. URLs and raw authentication headers stay out."""
    lines: list[str] = []
    list_id = headers.get("list_id", "").strip()
    if list_id:
        lines.append(f"List-Id: {_header_value(list_id)}")
    if headers.get("list_unsubscribe", "").strip():
        lines.append("List-Unsubscribe: present")
    labels = headers.get("labels", "").strip()
    if labels:
        lines.append(f"Labels: {_header_value(labels)}")
    if _spam_flag(headers):
        lines.append("Spam-Flag: yes")
    if dmarc_failed(headers):
        lines.append("DMARC: fail")
    if rspamd_is_spam(rspamd):
        lines.append("Rspamd: reject")
    elif rspamd_needs_review(rspamd) and not _structural_only(rspamd):
        lines.append("Rspamd: review")
    if not lines:
        return ""
    return "\n\nProvider signals:\n" + "\n".join(lines)
