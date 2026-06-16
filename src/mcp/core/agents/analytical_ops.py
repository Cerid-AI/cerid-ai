# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic analytical answer operators (extract → compute).

For analytical questions — date arithmetic and counting across memories — the
reasoning is offloaded from the LLM's mental math to a two-step pattern: the LLM
extracts the relevant facts as STRUCTURED data, then plain Python computes the
answer deterministically. This removes the two modal failure modes that survive
a good reader: off-by-one date errors and miscounting over scattered text.

Evidence:
- SPAN (arXiv 2511.09993): temporal QA 34.5% → 95.31% via an extract→execute
  loop (the LLM never does the arithmetic).
- QO-Bench (arXiv 2606.04646): even handed gold context, an LLM reader only
  counts at ~56% — a symbolic ``len()`` over a de-duplicated list is the fix.

Pure ``core`` primitive (no ``app`` imports) so the SAME operators are shared by
the LongMemEval eval pipeline and the production reader. Each operator makes ONE
structured-extraction LLM call and returns a final answer string, or ``None``
when the question isn't answerable this way (the caller falls back to normal
synthesis). The compute step is deterministic, side-effect-free, and unit-tested
without any LLM.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from core.utils.internal_llm import call_internal_llm
from core.utils.llm_parsing import parse_llm_json
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.analytical_ops")

# Stage breadcrumb for the structured-extraction call (provider-routable).
ANALYTICAL_STAGE = "analytical_extract"


# ---------------------------------------------------------------------------
# Pure, deterministic computation (no LLM) — fully unit-tested
# ---------------------------------------------------------------------------

_YMD_RE = re.compile(r"(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?")


def parse_iso_date(s: str) -> date | None:
    """Parse an ISO-ish date, filling partial dates (YYYY, YYYY-MM) to the 1st.

    Returns ``None`` for unparseable input rather than guessing — the caller
    treats a missing date as "not answerable deterministically".
    """
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = _YMD_RE.search(s)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 1
    day = int(m.group(3)) if m.group(3) else 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _months_between(start: date, end: date) -> int:
    """Whole months from start→end (floors partial months, like relativedelta)."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def compute_delta(start: date, end: date, unit: str, inclusive: bool) -> int:
    """Integer delta between two dates in the requested unit.

    ``inclusive`` adds the final endpoint (the "how many days were you there"
    +1 — the off-by-one that mental math gets wrong). Months/years use a
    calendar computation, not 30/365-day approximations.
    """
    days = (end - start).days
    if inclusive:
        days += 1 if days >= 0 else -1
    u = (unit or "days").lower()
    if u.startswith("day"):
        return days
    if u.startswith("week"):
        return days // 7 if days >= 0 else -((-days) // 7)
    if u.startswith("month"):
        return _months_between(start, end)
    if u.startswith("year"):
        return _months_between(start, end) // 12
    return days


def dedup_items(items: list[str]) -> list[str]:
    """De-duplicate item strings by a normalized key (exact key dominates).

    Preserves first-seen surface form. The normalized key collapses case,
    punctuation and whitespace so "Kyoto trip" ≡ "kyoto  trip" — but NOT
    "Kyoto"/"Osaka" (pure-semantic merging would re-introduce undercounting,
    so we deliberately keep the exact key).
    """
    seen: dict[str, str] = {}
    for it in items:
        key = re.sub(r"[^a-z0-9]+", " ", str(it).lower()).strip()
        if key and key not in seen:
            seen[key] = str(it).strip()
    return list(seen.values())


# ---------------------------------------------------------------------------
# LLM-driven extraction → deterministic compute
# ---------------------------------------------------------------------------

_TEMPORAL_PROMPT = """\
Extract the two dated events needed to answer a temporal question, so a \
calculator can compute the result. Use ONLY the supplied memories (each may be \
tagged "[recorded <date>]").

Memories:
{memory_block}

Question: {question}
{reference}
Return ONLY a JSON object:
{{"answerable": true or false,
  "start_event": {{"label": "<earlier event>", "iso_date": "YYYY-MM-DD"}},
  "end_event": {{"label": "<later event>", "iso_date": "YYYY-MM-DD"}},
  "unit": "days" | "weeks" | "months" | "years",
  "endpoints_inclusive": true or false}}

Rules:
- "how long ago" / "how many <unit> ago" / "how long since" / "how many <unit>
  since" are RELATIVE-TO-NOW: set end_event to the reference ("now") date above,
  and start_event to the dated event in the memories. These are answerable when
  the ONE event is present (you do not need a second event from the memories).
- Otherwise (e.g. "how long between X and Y") both events must be in the memories,
  else answerable=false.
- Resolve each event to an absolute ISO date using the memories' recorded dates.
- unit = the unit the question asks for (default "days").
- endpoints_inclusive=true ONLY if the question counts both endpoints (e.g.
  "how many days were you there" includes the last day); otherwise false.
"""

_COUNT_PROMPT = """\
List every DISTINCT item needed to answer a counting/aggregation question, so a \
program can count them. Use ONLY the supplied memories; the relevant items are \
often spread across multiple sessions.

Memories:
{memory_block}

Question: {question}

Return ONLY a JSON object:
{{"answerable": true or false, "items": ["distinct item 1", "distinct item 2"]}}

Rules:
- List each DISTINCT item exactly once (merge items that refer to the same thing;
  keep genuinely different items separate).
- Be exhaustive — scan EVERY memory across all sessions.
- answerable=false only if no relevant items are present.
"""


async def _extract_json(
    question: str, memory_block: str, prompt: str, reference: str = "",
) -> dict[str, Any] | None:
    try:
        raw = await call_internal_llm(
            [{"role": "user", "content": prompt.format(
                memory_block=memory_block, question=question, reference=reference,
            )}],
            temperature=0.0,
            max_tokens=400,
            response_format={"type": "json_object"},
            stage=ANALYTICAL_STAGE,
        )
    except Exception as exc:  # noqa: BLE001 — fall back to normal synthesis
        log_swallowed_error("core.agents.analytical_ops.extract", exc)
        return None
    data = parse_llm_json(raw)
    return data if isinstance(data, dict) else None


async def compute_temporal_answer(
    question: str, memory_block: str, reference_date: str | None = None,
) -> str | None:
    """Answer a temporal question by extracting dates and computing in code.

    ``reference_date`` is the "now" the question is asked at — required for the
    relative class ("how many weeks AGO / how long SINCE"), where one endpoint is
    the present, not a memory. Production passes today's date; the eval passes
    the item's ``question_date``. Returns ``"<n> <unit>"`` (magnitude), or
    ``None`` when the needed dates aren't extractable (caller falls back).
    """
    reference = (
        f'Reference ("now", the date this question is asked): {reference_date}'
        if reference_date else ""
    )
    data = await _extract_json(question, memory_block, _TEMPORAL_PROMPT, reference)
    if not data or not data.get("answerable"):
        return None
    start = parse_iso_date(str((data.get("start_event") or {}).get("iso_date", "")))
    end = parse_iso_date(str((data.get("end_event") or {}).get("iso_date", "")))
    if not start or not end:
        return None
    unit = str(data.get("unit", "days")) or "days"
    inclusive = bool(data.get("endpoints_inclusive", False))
    n = abs(compute_delta(start, end, unit, inclusive))
    return f"{n} {unit}"


async def compute_count_answer(question: str, memory_block: str) -> str | None:
    """Answer a counting question by extracting a list and counting in code.

    Returns ``"<n>"`` (plus the de-duplicated items for short lists), or
    ``None`` when no items are extractable.
    """
    data = await _extract_json(question, memory_block, _COUNT_PROMPT)
    if not data or not data.get("answerable"):
        return None
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return None
    items = dedup_items([str(i) for i in raw_items if str(i).strip()])
    if not items:
        return None
    n = len(items)
    if n <= 12:
        return f"{n}: {', '.join(items)}"
    return str(n)
