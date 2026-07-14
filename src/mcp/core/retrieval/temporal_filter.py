# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Time-anchored retrieval: boost dated chunks by proximity to a query time-window.

Pure ``core`` primitive (no ``app`` imports) shared by the eval retrieval
strategies and the production query path. The mechanism, per the LongMemEval
authors (+11.3% recall): an LLM extracts the absolute date RANGE a question is
about, and candidates whose recorded date falls in/near that range are
ADDITIVELY boosted — never filtered out, so a wrong/uncertain window can't drop
recall (the research's "soft constraint, not hard filter").

Three pieces:
- ``temporal_proximity`` — deterministic, unit-tested: 1.0 inside the window,
  exponential decay (by ``half_life_days``) outside.
- ``extract_query_window`` — one LLM call (stage ``longmemeval/temporal_parse``)
  returning the ISO window, or ``(None, None)`` when the question isn't
  time-scoped. Any failure degrades to no boost.
- ``apply_proximity_boost`` — anchors each candidate to its EVENT date (the
  date the content is actually about) when the candidate carries one,
  falling back to the existing ingest/observed-date basis otherwise (see
  ``_resolve_candidate_date``). A July LongMemEval paired A/B measured a
  statistical wash on the basis-only boost; the event-time basis is what
  makes it worth re-testing (Phase 2 item 2.7).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from core.agents.analytical_ops import parse_iso_date
from core.utils.internal_llm import call_internal_llm
from core.utils.llm_parsing import parse_llm_json
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.temporal_filter")

TEMPORAL_PARSE_STAGE = "longmemeval/temporal_parse"


def temporal_proximity(
    d: date | None,
    start: date | None,
    end: date | None,
    half_life_days: float = 30.0,
) -> float:
    """Proximity of a recorded date ``d`` to the window ``[start, end]`` in [0, 1].

    1.0 when ``d`` is inside the window (either bound may be open). Outside,
    decays as ``0.5 ** (gap_days / half_life_days)`` where ``gap_days`` is the
    distance to the nearest given bound — so a chunk a month off the window
    still gets a partial lift, but a year off gets ~nothing.
    """
    if d is None or (start is None and end is None):
        return 0.0
    after_start = start is None or d >= start
    before_end = end is None or d <= end
    if after_start and before_end:
        return 1.0
    gaps: list[int] = []
    if start is not None and d < start:
        gaps.append((start - d).days)
    if end is not None and d > end:
        gaps.append((d - end).days)
    if not gaps:
        return 0.0
    return 0.5 ** (min(gaps) / max(1.0, half_life_days))


_WINDOW_PROMPT = """\
{reference}What absolute date RANGE is this question asking about, for boosting \
dated notes recorded in that period? Reply ONLY a JSON object:
{{"scoped": true or false, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}

Rules:
- scoped=false if the question implies no particular time period.
- When only a year or month is implied, return the FULL range (e.g. all of 2023
  → start 2023-01-01, end 2023-12-31).
- For "last <period>" / "<n> <unit> ago", resolve against the reference date above.

Question: {question}"""


async def extract_query_window(
    question: str, reference_date: str | None = None,
) -> tuple[str | None, str | None]:
    """LLM-extract the ISO ``(start, end)`` window the question is scoped to.

    Returns ``(None, None)`` when the question isn't time-scoped or on any
    failure (so the caller simply applies no temporal boost).
    """
    reference = (
        f'Today (the date this question is asked) is {reference_date}. '
        if reference_date else ""
    )
    try:
        raw = await call_internal_llm(
            [{"role": "user", "content": _WINDOW_PROMPT.format(
                reference=reference, question=question,
            )}],
            temperature=0.0,
            max_tokens=60,
            response_format={"type": "json_object"},
            stage=TEMPORAL_PARSE_STAGE,
        )
    except Exception as exc:  # noqa: BLE001 — degrade to no boost
        log_swallowed_error("core.retrieval.temporal_filter.extract", exc)
        return None, None
    data = parse_llm_json(raw)
    if not isinstance(data, dict) or not data.get("scoped"):
        return None, None
    start = str(data.get("start") or "").strip() or None
    end = str(data.get("end") or "").strip() or None
    return start, end


def _resolve_candidate_date(
    candidate: dict[str, Any], event_date_key: str, date_key: str,
) -> str:
    """Prefer the EVENT date the content describes; fall back to ``date_key``.

    ``date_key`` (default ``"date"``) is typically an ingest/observed-date
    basis (e.g. session or recording date) — a proxy for "when it was
    recorded", not "when it happened". ``event_date_key`` (default
    ``"event_date"``), when present on the candidate, is the absolute date
    the content is actually ABOUT and is the correct anchor for a proximity
    boost. Candidates that carry no extracted event date degrade gracefully
    to the existing basis — this is a pure generalization, not a behavior
    change for callers that never populate ``event_date_key``.
    """
    return str(candidate.get(event_date_key) or candidate.get(date_key) or "")


def apply_proximity_boost(
    candidates: list[dict[str, Any]],
    start_iso: str | None,
    end_iso: str | None,
    weight: float,
    half_life_days: float,
    *,
    date_key: str = "date",
    event_date_key: str = "event_date",
    score_key: str = "score",
) -> int:
    """Additively lift each candidate's ``score`` by ``weight · proximity(date)``.

    The date anchoring each candidate is resolved by ``_resolve_candidate_date``
    — event time when available, else the existing basis (see there).

    Mutates ``candidates`` in place. Safe by construction: it only ever ADDS to
    scores (never removes a candidate), so an inaccurate window cannot reduce
    recall. Returns the number of candidates that received a non-zero lift (for
    telemetry). No-op when the window is empty.
    """
    start = parse_iso_date(start_iso) if start_iso else None
    end = parse_iso_date(end_iso) if end_iso else None
    if start is None and end is None:
        return 0
    lifted = 0
    for c in candidates:
        cd = parse_iso_date(_resolve_candidate_date(c, event_date_key, date_key))
        prox = temporal_proximity(cd, start, end, half_life_days)
        if prox > 0.0:
            c[score_key] = float(c.get(score_key, 0.0)) + weight * prox
            lifted += 1
    return lifted
