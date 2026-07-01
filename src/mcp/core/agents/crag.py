# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CRAG external-augmentation gate + firing for the canonical retrieval path.

The gate functions (``should_fire_external_crag`` / ``kb_low_confidence`` /
``freshest_kb_age_days``) are pure and were moved verbatim from
``app/routers/agents.py`` so the canonical *core* path owns them (Phase 1).
The firing (``augment_external_crag``) needs the app-side external-source
registry + search-term extractor; ``core/`` must never import ``app/``, so app
startup injects them via the DI setters (mirrors ``set_wiki_page_fetcher`` /
``set_data_source_registry``). Unwired → no-op (tests, public mirror, any caller
that hasn't wired external sources).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import config
from config.constants import EXTERNAL_SOURCE_QUERY_TIMEOUT

# ── DI seam (app injects at startup; core never imports app) ──────────────
_external_registry: Any = None
_search_term_extractor: Callable[[str], Any] | None = None


def set_external_source_registry(registry: Any) -> None:
    """Register the app-layer external DataSourceRegistry (called from app startup)."""
    global _external_registry
    _external_registry = registry


def set_search_term_extractor(fn: Callable[[str], Any]) -> None:
    """Register the app-layer search-term extractor (called from app startup)."""
    global _search_term_extractor
    _search_term_extractor = fn


# ── gate (pure; moved verbatim from app/routers/agents.py) ────────────────
def freshest_kb_age_days(kb_result: dict) -> float | None:
    """Return the age in days of the most recent KB result, or None.

    Reads ``created_at`` / ``ingested_at`` from each result. Returns ``None``
    when no result carries a parseable date — caller treats that as "unknown,
    do not apply the staleness rule".
    """
    from datetime import datetime

    from core.utils.time import utcnow

    results = kb_result.get("results") if isinstance(kb_result, dict) else None
    if not results:
        return None
    youngest_age: float | None = None
    now = utcnow().replace(tzinfo=None)
    for r in results:
        date_str = r.get("created_at") or r.get("ingested_at")
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
            dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
            age = (now - dt_naive).total_seconds() / 86400.0
        except (ValueError, TypeError):
            continue
        if age < 0:
            age = 0.0
        if youngest_age is None or age < youngest_age:
            youngest_age = age
    return youngest_age


def should_fire_external_crag(
    *,
    ext_on: bool,
    kb_result: dict,
    threshold: float,
    temporal_intent_days: int | None = None,
    freshest_kb_age_days: float | None = None,
    staleness_window_days: int | None = None,
) -> bool:
    """CRAG gate: decide whether to launch external sources.

    External sources are expensive (network I/O bounded by EXTERNAL_SOURCE_QUERY_TIMEOUT,
    circuit-breaker pressure). When KB already has a strong hit we skip them
    entirely — strong KB > any external result for the usual query mix, and the
    /agent/query wall-clock budget is precious.

    Fires when ANY is true:
      - the best KB relevance is strictly below `threshold`, OR
      - the query has temporal intent (``temporal_intent_days`` not None) AND
        the freshest KB result is older than ``staleness_window_days``.

    Always returns False when `ext_on=False`. A result set with no `results`
    key (or empty list) yields max=0.0, which is always < threshold — so
    unknown-KB correctly falls through to external.
    """
    if not ext_on:
        return False
    results = kb_result.get("results") if isinstance(kb_result, dict) else None
    if not results:
        return True

    max_rel = max((r.get("relevance", 0.0) for r in results), default=0.0)
    if max_rel < threshold:
        return True

    if temporal_intent_days is not None:
        if staleness_window_days is None:
            staleness_window_days = getattr(config, "CRAG_STALENESS_WINDOW_DAYS", 7)
        if freshest_kb_age_days is None or freshest_kb_age_days > staleness_window_days:
            return True

    return False


def kb_low_confidence(kb_result: dict, threshold: float) -> bool:
    """True when the best KB relevance is below the quality threshold (GA P0.5 B2a).

    Surfaced as ``low_confidence`` on the response so callers/UI can hedge a weak
    KB answer even when external augmentation fired. Mirrors
    ``should_fire_external_crag``'s max-relevance rule. Pure signal.
    """
    results = kb_result.get("results") if isinstance(kb_result, dict) else None
    if not results:
        return True
    max_rel = max((r.get("relevance", 0.0) for r in results), default=0.0)
    return max_rel < threshold


# ── firing (app-bound deps via DI; no-op when unwired) ────────────────────
_EXTERNAL_DISCOUNT = 0.6


async def augment_external_crag(
    result: dict,
    query: str,
    domains: list[str] | None,
    threshold: float,
) -> dict:
    """Fire external sources + merge into the result envelope when the CRAG gate
    trips. No-op when the registry/extractor are unwired. Mirrors the original
    app-side block (agents.py:432-481) now that it lives on the core path.
    """
    registry = _external_registry
    extractor = _search_term_extractor
    if registry is None or extractor is None:
        return result

    from core.utils.temporal import parse_temporal_intent

    temporal_days = parse_temporal_intent(query)
    freshest = freshest_kb_age_days(result) if temporal_days is not None else None
    if not should_fire_external_crag(
        ext_on=True,
        kb_result=result,
        threshold=threshold,
        temporal_intent_days=temporal_days,
        freshest_kb_age_days=freshest,
    ):
        return result

    ext_results: list = []
    try:
        search_terms = extractor(query)
        ext_results = await asyncio.wait_for(
            registry.query_all(
                search_terms,
                domain=domains[0] if domains else None,
                timeout=EXTERNAL_SOURCE_QUERY_TIMEOUT,
            ),
            timeout=EXTERNAL_SOURCE_QUERY_TIMEOUT + 1.0,
        )
    except (Exception, asyncio.TimeoutError):
        ext_results = []

    if ext_results:
        from core.models.query_envelope import QueryEnvelope, SourceItem

        env = QueryEnvelope.from_legacy_result(result)
        env.merge_external([
            SourceItem(
                content=r.get("content", ""),
                relevance=round(r.get("confidence", 0.8) * _EXTERNAL_DISCOUNT, 3),
                artifact_id="",
                filename=r.get("source_name", ""),
                source_type="external",
                domain="external",
                collection="external",
                source_url=r.get("source_url", ""),
                source_name=r.get("source_name", r.get("title", "")),
            )
            for r in ext_results
        ])
        result = env.to_dict()
    return result
