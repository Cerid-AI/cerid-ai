# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Claim freshness classification and evidence recency weighting.

Phase 3.4 of the 2026-07-13 quality-maximization program: splits claims into
``timeless`` / ``time_sensitive`` / ``dated`` so the verdict cache can apply a
short TTL to volatile claims regardless of which verification method produced
the verdict, and so KB evidence selection can prefer newer sources when the
claim itself is time-sensitive.

Pure module: no I/O, no LLM calls, no Redis/Chroma/Neo4j access — safe to
call unconditionally in the ``verify_claim`` hot path. Reuses the
hallucination subsystem's existing temporal-signal primitives
(``patterns._is_current_event_claim``, ``patterns._is_recency_claim``,
``patterns.YEAR_RE``) rather than re-deriving them: those functions answer
"does this claim need a live web check?" — a different, stricter question
than "how long can a verdict for this claim be cached?" — so this module
adds the freshness-specific keyword walk and the dated/timeless split on
top, instead of duplicating the escalation heuristics.

Heuristic-first by design: the keyword matching below is plain string-walk
containment checks, not regex, to stay clear of the DUO138 ReDoS lint gate.
The one regex reused here (``YEAR_RE``) is an existing, already-vetted
pattern imported from ``patterns.py``, not a new one.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from core.agents.hallucination.patterns import (
    YEAR_RE,
    _is_current_event_claim,
    _is_recency_claim,
)
from core.utils.time import utcnow

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class ClaimFreshness(str, Enum):
    """Cache-TTL-relevant freshness class for a verified claim."""

    TIMELESS = "timeless"
    TIME_SENSITIVE = "time_sensitive"
    DATED = "dated"


# Phrases that mark a claim as inherently volatile — a value that can change
# independent of any date the claim might also mention. Unlike
# ``_is_current_event_claim`` (which requires two weak signals, or one strong
# one, to justify an expensive web-search escalation), a SINGLE match here is
# enough to cap the cache TTL: the cost of a false positive is just an extra
# re-verification, while the cost of a false negative is serving a stale
# price/version/leadership claim for up to 30 days.
_PRICE_PHRASES: tuple[str, ...] = (
    "price", "prices", "priced", "pricing", "cost", "costs", "fee", "fees",
    "fare", "fares", "rate", "rates", "charge", "charges", "subscription",
    "salary", "salaries", "wage", "wages",
)
_VERSION_PHRASES: tuple[str, ...] = (
    "latest version", "current version", "newest version",
    "latest release", "current release", "most recent version",
)
_LEADERSHIP_PHRASES: tuple[str, ...] = (
    "current ceo", "current president", "current chairman",
    "current chairperson", "current prime minister", "acting ceo",
    "interim ceo", "current leader", "current chief executive",
)
_RECORD_PHRASES: tuple[str, ...] = (
    "current record", "record holder", "world record", "record high", "record low",
)
_SCHEDULE_PHRASES: tuple[str, ...] = (
    "upcoming", "scheduled", "next release", "next event", "next election",
)
_CURRENCY_PHRASES: tuple[str, ...] = (
    "currently", "as of now", "at present", "right now", "presently",
    "latest", "newest", "most recent", "up to date",
)

_TIME_SENSITIVE_PHRASES: tuple[str, ...] = (
    _PRICE_PHRASES
    + _VERSION_PHRASES
    + _LEADERSHIP_PHRASES
    + _RECORD_PHRASES
    + _SCHEDULE_PHRASES
    + _CURRENCY_PHRASES
)


def _normalize_for_keyword_scan(text: str) -> str:
    """Lowercase and replace punctuation with single spaces.

    Plain character walk (no regex) — keeps this module clear of the
    DUO138-flagged pattern shapes. Returns a space-padded string so callers
    can do whole-word/phrase containment via ``f" {phrase} " in normalized``
    without matching inside a larger word (e.g. "rate" must not match inside
    "corporate").
    """
    chars: list[str] = []
    prev_space = True
    for ch in text.lower():
        if ch.isalnum():
            chars.append(ch)
            prev_space = False
        elif not prev_space:
            chars.append(" ")
            prev_space = True
    return f" {''.join(chars).strip()} "


def _contains_any_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    return any(f" {phrase} " in normalized for phrase in phrases)


def classify_claim_freshness(claim: str) -> ClaimFreshness:
    """Classify a claim's cache-TTL-relevant freshness.

    Ordering is deliberate and safety-biased:

    1. Time-sensitive language wins over a co-occurring date — "the latest
       version, released in 2023, costs $99" is TIME_SENSITIVE despite the
       explicit year, because the price/version framing means the verified
       fact can go stale independent of that date.
    2. Any time-sensitive signal (either the phrase walk above, or the
       broader existing ``_is_current_event_claim`` / ``_is_recency_claim``
       heuristics) short-circuits straight to TIME_SENSITIVE. This is the
       "default safely" choice for ambiguous claims: capping the cache TTL
       too aggressively costs one extra re-verification; capping it too
       loosely can serve a stale volatile claim for up to 30 days.
    3. A claim with no time-sensitive signal but an explicit year/date is
       DATED — a fixed historical snapshot that, once verified, does not
       need re-checking on the short clock.
    4. Everything else (no date, no volatility markers — definitional,
       mathematical, settled historical claims) is TIMELESS, the default
       for a claim that offers no reason to expire it early.
    """
    normalized = _normalize_for_keyword_scan(claim)
    if _contains_any_phrase(normalized, _TIME_SENSITIVE_PHRASES):
        return ClaimFreshness.TIME_SENSITIVE
    if _is_current_event_claim(claim) or _is_recency_claim(claim):
        return ClaimFreshness.TIME_SENSITIVE
    if YEAR_RE.search(claim):
        return ClaimFreshness.DATED
    return ClaimFreshness.TIMELESS


# ---------------------------------------------------------------------------
# Evidence date-stamping + recency weighting
# ---------------------------------------------------------------------------

# Bounded recency bonus for evidence re-ranking. Small relative to the
# relevance scores (0.0-1.0 scale) it's added to — enough to break a
# near-tie in favour of newer evidence, never enough to override a
# meaningfully stronger match with a barely-fresher weak one.
RECENCY_WEIGHT_MAX_BONUS = 0.05

# Evidence older than this contributes no bonus; the falloff is linear
# between "today" (full bonus) and this horizon (zero bonus).
RECENCY_WEIGHT_HORIZON_DAYS = 365.0

# Below this many candidates there is nothing to reorder.
_MIN_RESULTS_FOR_RECENCY_WEIGHTING = 2

# Cheap fallback year-extraction bounds and scan window (see
# ``extract_year_from_text`` / ``evidence_age_days``).
_MIN_PLAUSIBLE_YEAR = 1900
_YEAR_TOKEN_LENGTH = 4
_EVIDENCE_TEXT_SCAN_CHARS = 500
_SECONDS_PER_DAY = 86400.0


def _parse_evidence_date(date_str: Any) -> datetime | None:
    """Parse a metadata date string; ``None`` on anything unparseable.

    Mirrors ``verification._evidence_is_stale``'s tolerant ISO parsing so
    the two stay behaviourally consistent, without importing across that
    module boundary for a few shared lines.
    """
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError):
        return None


def extract_year_from_text(text: str, *, current_year: int | None = None) -> int | None:
    """Return the most recent plausible 4-digit year token found in ``text``.

    Plain string walk (no regex). Deliberately a coarse heuristic, not a
    date parser: used only as a fallback when evidence carries no
    ``created_at``/``ingested_at`` metadata. A 4-digit run outside
    ``[1900, current_year + 1]`` is treated as noise (a model number, a
    port, a percentage) rather than a year.
    """
    if current_year is None:
        current_year = utcnow().year
    best: int | None = None
    i, n = 0, len(text)
    while i < n:
        if text[i].isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            token = text[i:j]
            if len(token) == _YEAR_TOKEN_LENGTH:
                year = int(token)
                if _MIN_PLAUSIBLE_YEAR <= year <= current_year + 1 and (best is None or year > best):
                    best = year
            i = j
        else:
            i += 1
    return best


def evidence_age_days(evidence: dict[str, Any]) -> float | None:
    """Best-effort age of a KB evidence item in days, or ``None`` if unknown.

    Prefers ``created_at``/``ingested_at`` metadata — present on KB results
    since the RAG Phase 1.1 provenance spine
    (``core.agents.query_agent._format_chroma_result``). Falls back to a
    coarse year-token scan of the evidence text (Phase 3.4: date coverage on
    the metadata path is good for KB chunks but not guaranteed for every
    result shape, e.g. older ingests or the memory-query path). Returns
    ``None`` — never a manufactured age — when neither source yields a
    date, so callers treat "unknown" as "no bonus, no penalty" rather than
    guessing.
    """
    dt = _parse_evidence_date(evidence.get("created_at") or evidence.get("ingested_at"))
    if dt is None:
        year = extract_year_from_text(
            (evidence.get("content") or "")[:_EVIDENCE_TEXT_SCAN_CHARS]
        )
        if year is None:
            return None
        dt = datetime(year, 1, 1)
    age_seconds = (utcnow().replace(tzinfo=None) - dt).total_seconds()
    return max(0.0, age_seconds / _SECONDS_PER_DAY)


def weight_evidence_by_recency(
    results: list[dict[str, Any]],
    freshness: ClaimFreshness,
) -> list[dict[str, Any]]:
    """Re-order KB evidence to prefer newer sources for time-sensitive claims.

    No-op (returns ``results`` unchanged) for TIMELESS/DATED claims or fewer
    than two candidates — a settled historical or definitional claim doesn't
    benefit from picking the newest match; "newer" isn't "more correct" for
    something that hasn't changed.

    Bounded additive adjustment on top of the existing relevance score
    (``RECENCY_WEIGHT_MAX_BONUS``), not a rewrite of evidence selection: it
    can flip the order of two closely-relevant results but cannot push a
    weak, fresh result above a much stronger, older one.
    """
    if freshness != ClaimFreshness.TIME_SENSITIVE or len(results) < _MIN_RESULTS_FOR_RECENCY_WEIGHTING:
        return results

    def _sort_key(evidence: dict[str, Any]) -> float:
        relevance = evidence.get("relevance", 0.0)
        age_days = evidence_age_days(evidence)
        if age_days is None:
            return relevance
        recency_fraction = max(0.0, 1.0 - min(age_days, RECENCY_WEIGHT_HORIZON_DAYS) / RECENCY_WEIGHT_HORIZON_DAYS)
        return relevance + RECENCY_WEIGHT_MAX_BONUS * recency_fraction

    return sorted(results, key=_sort_key, reverse=True)
