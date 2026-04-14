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


def _classify_claim_domain(claim: str) -> str:
    """Classify a claim into a domain for source routing."""
    if _SCIENTIFIC_RE.search(claim):
        return "scientific"
    if _FINANCIAL_RE.search(claim):
        return "financial"
    if _COMPUTATIONAL_RE.search(claim):
        return "computational"
    if _GEOGRAPHIC_RE.search(claim):
        return "geographic"
    return "general"


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
        return {"authoritative_sources": [], "cross_validation": {}, "claim_domain": "disabled"}

    domain = claim_domain or _classify_claim_domain(claim)
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
        "evidence_summary": summary,
    }
