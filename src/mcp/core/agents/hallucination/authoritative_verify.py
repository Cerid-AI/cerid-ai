# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Authoritative external verification — expert mode uses real data, not just a bigger LLM.

Instead of routing "expert verification" to a more expensive LLM model (which
still hallucinates from parametric memory), this module:

1. Classifies the claim's domain (scientific, financial, geographic, etc.)
2. Queries domain-appropriate authoritative data sources via the DataSourceRegistry
3. Runs NLI entailment between the claim and each external source result
4. Cross-validates KB results against external results (catches KB staleness)
5. Returns structured evidence that the LLM verifier uses as input context

The LLM is the **synthesis engine** — external data is the **source of truth**.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any

import config

logger = logging.getLogger("ai-companion.authoritative_verify")

# Domain classification patterns — maps queries to their most authoritative sources
_SCIENTIFIC_RE = re.compile(
    r"\b(?:chemical|compound|molecule|molecular|drug|pharmaceutical|element|"
    r"atom|reaction|protein|gene|enzyme|bacteria|virus|cell|"
    r"DNA|RNA|clinical|trial|study|research|peer.review|journal)\b",
    re.I,
)
_FINANCIAL_RE = re.compile(
    r"\b(?:stock|bond|market|price|GDP|inflation|interest rate|"
    r"currency|exchange|dollar|euro|revenue|profit|valuation|"
    r"fiscal|monetary|economy|economic)\b",
    re.I,
)
_COMPUTATIONAL_RE = re.compile(
    r"\b(?:calculate|compute|solve|integrate|derive|convert|"
    r"evaluate|formula|equation|sum|product|square root|"
    r"factorial|logarithm|trigonometr)\b",
    re.I,
)
_GEOGRAPHIC_RE = re.compile(
    r"\b(?:population|capital|country|city|continent|area|"
    r"river|mountain|ocean|lake|border|region|territory|"
    r"latitude|longitude|geography)\b",
    re.I,
)

# (name, pattern, priority_weight). Priority order reflects which source
# registry gets queried when a claim tokens land in multiple domains — e.g.
# "how has the stock price of pharmaceutical companies like Pfizer changed"
# matches both financial and scientific, and scientific is the more
# authoritative routing target for empirical verification.
_DOMAIN_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    ("scientific", _SCIENTIFIC_RE, 1.00),
    ("financial", _FINANCIAL_RE, 0.90),
    ("computational", _COMPUTATIONAL_RE, 0.85),
    ("geographic", _GEOGRAPHIC_RE, 0.80),
]


def _score_claim_domains(claim: str) -> dict[str, float]:
    """Per-domain score = (1 + log(match_count)) × priority_weight.

    log scaling keeps keyword-heavy claims from runaway-dominating; priority
    weights break ties in favour of the higher-authority source registry.
    Empty dict means no domain matched (→ "general").
    """
    scores: dict[str, float] = {}
    for name, pattern, weight in _DOMAIN_PATTERNS:
        match_count = len(pattern.findall(claim))
        if match_count:
            scores[name] = round((1.0 + math.log(match_count)) * weight, 3)
    return scores


def _classify_claim_domain(claim: str) -> str:
    """Return the best-matching domain by score, or 'general' if none match.

    Backward-compat wrapper around _classify_claim_domain_detailed() for
    callers that only need the primary label. Previous version short-
    circuited on the first regex hit with no weighting, which mis-routed
    ambiguous claims (e.g. ``pharmaceutical stock price`` → financial
    when the empirical-evidence need is scientific).
    """
    return _classify_claim_domain_detailed(claim)["primary"]


def _classify_claim_domain_detailed(claim: str) -> dict[str, Any]:
    """Detailed classification with confidence + secondary matches.

    Returns::

        {
            "primary": str,              # best-scoring domain (or "general")
            "confidence": float,         # 0-1, how decisive the primary is
            "secondary": list[tuple[str, float]],  # (domain, score) sorted desc
        }

    Confidence is the normalized margin between the primary score and the
    next-highest score: 1.0 when only one domain matches, 0 when the top
    two tie. Downstream callers can use a low confidence signal to query
    multiple source registries instead of committing to a single domain.
    """
    scores = _score_claim_domains(claim)
    if not scores:
        return {"primary": "general", "confidence": 0.0, "secondary": []}

    sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    primary, primary_score = sorted_scores[0]
    secondary = sorted_scores[1:]

    if not secondary:
        confidence = 1.0
    else:
        second_score = secondary[0][1]
        total = primary_score + second_score
        confidence = round((primary_score - second_score) / total, 3) if total > 0 else 0.5

    return {
        "primary": primary,
        "confidence": confidence,
        "secondary": [(n, s) for n, s in secondary],
    }


async def verify_claim_authoritatively(
    claim: str,
    kb_results: list[dict[str, Any]] | None = None,
    memories: list[dict[str, Any]] | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    claim_domain: str | None = None,
) -> dict[str, Any]:
    """Multi-source authoritative verification pipeline.

    Queries external data sources for empirical evidence, runs NLI entailment
    between the claim and each source result, and cross-validates KB results
    against external evidence.

    Returns:
        {
            "authoritative_sources": [{"source": str, "content": str, "nli_entailment": float}],
            "cross_validation": {"kb_vs_external_agreement": float},
            "claim_domain": str,
            "evidence_summary": str,
        }
    """
    if not getattr(config, "EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", True):
        return {
            "authoritative_sources": [],
            "cross_validation": {},
            "claim_domain": "disabled",
            "domain_confidence": 0.0,
            "secondary_domains": [],
        }

    # Detailed classification — primary for source routing, confidence +
    # secondary for downstream consumers that want to query multiple
    # registries on ambiguous claims.
    if claim_domain is not None:
        domain = claim_domain
        domain_detail: dict[str, Any] = {"primary": claim_domain, "confidence": 1.0, "secondary": []}
    else:
        domain_detail = _classify_claim_domain_detailed(claim)
        domain = domain_detail["primary"]

    max_sources = getattr(config, "EXPERT_VERIFY_MAX_SOURCES", 3)

    # Step 1: Query authoritative external sources via the DataSourceRegistry
    external_results: list[dict] = []
    try:
        from utils.data_sources import registry
        from utils.metadata import _extract_keywords_simple

        keywords = _extract_keywords_simple(claim, max_keywords=5)
        search_terms = " ".join(keywords) if keywords else claim

        raw_results = await asyncio.wait_for(
            registry.query_all(
                search_terms,
                timeout=4.0,
                raw_query=claim,
                keywords=keywords,
            ),
            timeout=5.0,
        )
        external_results = raw_results[:max_sources]
    except Exception:
        logger.debug("Authoritative source query failed (non-blocking)")

    if not external_results:
        return {
            "authoritative_sources": [],
            "cross_validation": {},
            "claim_domain": domain,
            "domain_confidence": domain_detail["confidence"],
            "secondary_domains": domain_detail["secondary"],
            "evidence_summary": "No authoritative sources returned results.",
        }

    # Step 2: NLI entailment between claim and each external result
    scored_sources: list[dict[str, Any]] = []
    try:
        from core.utils.nli import nli_score

        for ext in external_results:
            content = ext.get("content", "")[:512]
            if not content:
                continue
            nli = nli_score(content, claim)
            scored_sources.append({
                "source": ext.get("source_name", "unknown"),
                "content": content[:200],
                "source_url": ext.get("source_url", ""),
                "nli_entailment": float(nli["entailment"]),
                "nli_contradiction": float(nli["contradiction"]),
                # Provenance for staleness auditing — sources may provide
                # last_updated / data_freshness / published / retrieved_at
                # fields (data_sources registry), or none (defaults to
                # "unknown" so downstream reports can render an honest
                # "freshness unknown" label instead of dropping the field).
                "data_freshness": ext.get("last_updated")
                    or ext.get("data_freshness")
                    or ext.get("published")
                    or ext.get("retrieved_at", "unknown"),
            })
    except Exception:
        logger.debug("NLI scoring of authoritative sources failed")
        # Fall back to unscored sources
        for ext in external_results:
            scored_sources.append({
                "source": ext.get("source_name", "unknown"),
                "content": ext.get("content", "")[:200],
                "source_url": ext.get("source_url", ""),
                "nli_entailment": 0.0,
                "nli_contradiction": 0.0,
                "data_freshness": ext.get("last_updated")
                    or ext.get("data_freshness")
                    or ext.get("published")
                    or ext.get("retrieved_at", "unknown"),
            })

    # Step 3: Cross-validate KB results against external evidence
    cross_validation: dict[str, Any] = {}
    if kb_results and scored_sources:
        try:
            from core.utils.nli import nli_score

            kb_text = kb_results[0].get("content", "")[:512] if kb_results else ""
            ext_text = scored_sources[0].get("content", "")[:512] if scored_sources else ""
            if kb_text and ext_text:
                cross_nli = nli_score(kb_text, ext_text)
                cross_validation = {
                    "kb_vs_external_agreement": float(cross_nli["entailment"]),
                    "kb_vs_external_contradiction": float(cross_nli["contradiction"]),
                }
        except Exception:
            logger.debug("KB vs external cross-validation failed")

    # Step 4: Build evidence summary for the LLM verifier
    supporting = [s for s in scored_sources if s["nli_entailment"] >= 0.5]
    contradicting = [s for s in scored_sources if s["nli_contradiction"] >= 0.5]

    if supporting:
        summary = f"{len(supporting)} authoritative source(s) support the claim."
    elif contradicting:
        summary = f"{len(contradicting)} authoritative source(s) contradict the claim."
    else:
        summary = f"{len(scored_sources)} source(s) queried — no strong entailment or contradiction."

    return {
        "authoritative_sources": scored_sources,
        "cross_validation": cross_validation,
        "claim_domain": domain,
        "domain_confidence": domain_detail["confidence"],
        "secondary_domains": domain_detail["secondary"],
        "evidence_summary": summary,
    }
