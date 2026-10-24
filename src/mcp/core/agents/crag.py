# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
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
from config.constants import (
    EXTERNAL_SOURCE_QUERY_TIMEOUT,
    EXTERNAL_SOURCE_RELEVANCE_DISCOUNT,
)
from core.utils.swallowed import log_swallowed_error

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
      - the KB's answer strength (:func:`kb_answer_strength`) is strictly
        below `threshold`, OR
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

    if kb_answer_strength(results) < threshold:
        return True

    if temporal_intent_days is not None:
        if staleness_window_days is None:
            staleness_window_days = getattr(config, "CRAG_STALENESS_WINDOW_DAYS", 7)
        # A query that names its own window ("this month", "this year") is
        # answered by anything inside that window, so hold it to that window
        # rather than the 7-day floor, which treated in-window KB rows as stale
        # and fired external over a strong hit. Short intents (today, this
        # week, recent) still sit on the floor.
        window = max(staleness_window_days, temporal_intent_days)
        if freshest_kb_age_days is None or freshest_kb_age_days > window:
            return True

    return False


def kb_answer_strength(results: list[dict]) -> float:
    """How well the KB rows answer the query: the best cross-encoder score where
    the reranker judged them, else the best relevance.

    Relevance after reranking blends the cross-encoder with the retrieval score
    (0.4 / 0.6), so a row the cross-encoder rejects still scores near 0.5 on its
    cosine alone, and a compiled wiki page (retrieval 1.0) never drops below 0.6.
    Gated on that blend, questions nothing in the KB answers read as answered
    (audit F07). The cross-encoder's own score is the answerability signal.
    """
    judged = [r["rerank_score"] for r in results if r.get("rerank_score") is not None]
    if judged:
        return max(judged)
    return max((r.get("relevance", 0.0) for r in results), default=0.0)


def kb_low_confidence(kb_result: dict, threshold: float) -> bool:
    """True when the best KB relevance is below the quality threshold (GA P0.5 B2a).

    Surfaced as ``low_confidence`` on the response so callers/UI can hedge a weak
    KB answer even when external augmentation fired. Same rule as
    ``should_fire_external_crag``. Pure signal.
    """
    results = kb_result.get("results") if isinstance(kb_result, dict) else None
    if not results:
        return True
    return kb_answer_strength(results) < threshold


# ── firing (app-bound deps via DI; no-op when unwired) ────────────────────


def crag_wants_external(result: Any, query: str, threshold: float) -> bool:
    """The one CRAG decision: should this query go to external sources?

    No for a skipped retrieval (a greeting, a consumer limited away from every
    domain: there is no low-confidence KB to correct, and sending the query out
    would leak it for nothing). Otherwise the gate fires on a weak KB, or on a
    question about freshness whose answering rows are all stale. Manual mode (through
    ``augment_external_crag``) and smart mode both ask here.
    """
    if not isinstance(result, dict) or result.get("retrieval_skipped"):
        return False
    from core.agents.external_query import freshness_intent

    temporal_days = freshness_intent(query)
    # How fresh the answer is depends on the rows that answer, not on whatever
    # row was ingested last: a Python docs pack from July answered "the latest
    # stable release of Python" while an unrelated row from yesterday made the
    # whole result look fresh.
    answering = _answering_rows(result.get("results") or [], threshold)
    return should_fire_external_crag(
        ext_on=True,
        kb_result=result,
        threshold=threshold,
        temporal_intent_days=temporal_days,
        freshest_kb_age_days=(
            freshest_kb_age_days({"results": answering}) if temporal_days is not None else None
        ),
    )


def _answering_rows(results: list[dict], threshold: float) -> list[dict]:
    """The rows strong enough to answer, on the scale :func:`kb_answer_strength` reads."""
    judged = [r for r in results if r.get("rerank_score") is not None]
    if judged:
        return [r for r in judged if r["rerank_score"] >= threshold]
    return [r for r in results if r.get("relevance", 0.0) >= threshold]


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
    if not crag_wants_external(result, query, threshold):
        return result

    from core.agents.external_query import external_query_for

    outgoing = await external_query_for(query)
    if outgoing is None:
        return result

    ext_results: list = []
    try:
        search_terms = extractor(outgoing)
        ext_results = await asyncio.wait_for(
            registry.query_all(
                search_terms,
                domain=domains[0] if domains else None,
                timeout=EXTERNAL_SOURCE_QUERY_TIMEOUT,
                raw_query=outgoing,
                keywords=search_terms.split(),
            ),
            timeout=EXTERNAL_SOURCE_QUERY_TIMEOUT + 1.0,
        )
    except asyncio.TimeoutError:
        ext_results = []  # external sources over budget — expected, not swallowed
    except Exception as exc:
        log_swallowed_error(f"{__name__}.external_query", exc)
        ext_results = []

    if ext_results:
        from core.models.external_evidence import ExternalEvidence
        from core.models.query_envelope import QueryEnvelope, SourceItem

        items = []
        for r in ext_results:
            ev = ExternalEvidence.from_mapping(r)
            # confidence-absent → 0.8 (legacy default; a real 0.0 stays 0.0).
            rel = ev.relevance if ev.relevance is not None else 0.8
            items.append(SourceItem(
                content=ev.content,
                relevance=round(rel * EXTERNAL_SOURCE_RELEVANCE_DISCOUNT, 3),
                artifact_id="",
                filename=ev.source_name,
                source_type="external",
                domain="external",
                collection="external",
                source_url=ev.url,
                source_name=ev.source_name or ev.title,
                guid=ev.guid or "",
            ))
        rows = [i.to_dict() for i in items]
        kept = {id(r) for r in await judge_external(query, rows)}
        for item, row in zip(items, rows, strict=True):
            item.relevance = row["relevance"]
        items = [i for i, r in zip(items, rows, strict=True) if id(r) in kept]
        rejected = int(result.get("rerank_rejected") or 0) + len(rows) - len(items)
        if result.get("source_breakdown"):
            env = QueryEnvelope.from_legacy_result(result)
            env.merge_external(items)
            result = env.to_dict()
        else:
            result = _append_external(result, [i.to_dict() for i in items])
        result["rerank_rejected"] = rejected
        # The caller's top_k cap comes after this merge: external hits compete
        # for it on relevance instead of queueing behind every KB row.
        result["results"] = _by_relevance(result.get("results", []))
        result["sources"] = _by_relevance(result.get("sources", []))
    return result


async def judge_external(query: str, rows: list[dict]) -> list[dict]:
    """Score external rows with the reranker the KB rows went through, and keep
    the ones it accepts, under the KB's rule.

    A source's confidence is a constant (every Wikipedia page 0.85, a web hit
    by its rank), so the 'Celsius' page outranked the hit that answered 'boiling
    point of ethanol in Celsius'. Reranked, an external row's relevance is the
    KB blend: 0.4 x cross-encoder + 0.6 x its discounted confidence.
    """
    from core.agents.query_agent import rerank_results, vetted_by_cross_encoder

    await rerank_results(rows, query)
    return vetted_by_cross_encoder(rows)


def _by_relevance(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: float(r.get("relevance") or 0.0), reverse=True)


def _append_external(result: dict, external: list[dict]) -> dict:
    """Merge external rows into the dict ``agent_query`` returns, dropping nothing.

    That dict carries no ``source_breakdown``. Its ``results`` are the raw
    chunks and its ``sources`` the context-included citations — two different
    lists, both carrying fields ``SourceItem`` does not model (chunk_index,
    created_at, pack_id, custom metadata, table provenance, and ``wiki`` as a
    source_type). Rebuilding it through ``QueryEnvelope.from_legacy_result``
    found no breakdown to read, so every KB row vanished and the caller got the
    external hits alone (F104). Append instead, and derive the breakdown from
    ``results`` so ``results == flatten(source_breakdown)`` holds.
    """
    results = [*result.get("results", []), *external]
    sources = [*result.get("sources", []), *external]
    out = dict(result)
    out["results"] = results
    out["sources"] = sources
    out["total_results"] = len(results)
    out["source_breakdown"] = {
        "kb": [r for r in results if r.get("source_type", "kb") not in ("memory", "external")],
        "memory": [r for r in results if r.get("source_type") == "memory"],
        "external": [r for r in results if r.get("source_type") == "external"],
    }
    if sources:
        out["confidence"] = round(
            sum(float(s.get("relevance", 0.0)) for s in sources) / len(sources), 4,
        )
    return out
