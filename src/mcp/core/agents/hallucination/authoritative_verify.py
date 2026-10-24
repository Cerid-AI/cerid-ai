# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
from typing import Any, Protocol

import config
from config.constants import AUTHORITATIVE_VERIFY_QUERY_TIMEOUT
from core.models.external_evidence import ExternalEvidence

logger = logging.getLogger("ai-companion.authoritative_verify")


class _RegistryProtocol(Protocol):
    """Structural type for the DataSourceRegistry dependency.

    Declared here so ``core/`` can type-reference the registry surface
    without importing ``app.data_sources`` at runtime — that would re-
    introduce the core→app violation this sprint removed.
    """

    async def query_all(
        self,
        search_terms: str,
        *,
        timeout: float = ...,
        raw_query: str = ...,
        keywords: list[str] = ...,
    ) -> list[dict[str, Any]]: ...


_registry: _RegistryProtocol | None = None


def set_data_source_registry(registry: _RegistryProtocol) -> None:
    """Wire the DataSourceRegistry in from app-land at startup.

    App startup (``app/main.py``) calls this once with the concrete
    ``app.data_sources.registry`` singleton. Downstream core callers then
    fetch it via the module-level default without importing ``app.*``.
    """
    global _registry
    _registry = registry


def get_data_source_registry() -> _RegistryProtocol | None:
    """Return the app-registered DataSourceRegistry, or None if unwired."""
    return _registry

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
    *,
    registry: _RegistryProtocol | None = None,
) -> dict[str, Any]:
    """Multi-source authoritative verification pipeline.

    Queries external data sources for empirical evidence, runs NLI entailment
    between the claim and each source result, and cross-validates KB results
    against external evidence.

    Returns:
        {
            "authoritative_sources": [{"source": str, "content": str, "nli_entailment": float,
                                       "nli_scored": bool}],
            "cross_validation": {"kb_vs_external_agreement": float, "compared_source": str}
                                | {"skipped": str},
            "claim_domain": str,
            "evidence_summary": str,
            "query_error": str,  # present only when the source query itself failed
        }

    NLI scores are class probabilities (0-1). An entry with ``nli_scored`` False
    was not scored; its 0.0 values are placeholders, not weak evidence.
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

    # Step 1: Query authoritative external sources via the DataSourceRegistry.
    # Registry is injected — either passed explicitly or resolved from the
    # module-level provider that ``app/main.py`` wires at startup. Keeps
    # ``core/`` free of runtime ``app.*`` imports.
    external_results: list[dict] = []
    query_error: str | None = None
    effective_registry = registry if registry is not None else _registry
    if effective_registry is None:
        logger.debug(
            "No DataSourceRegistry configured — skipping authoritative source query. "
            "Call set_data_source_registry() from app startup to wire one."
        )
    else:
        try:
            from core.agents.external_query import external_query_for_claim
            from core.utils.text import extract_keywords_simple

            # A claim about the user's own data goes out only as a public-fact
            # query, or not at all.
            outgoing = await external_query_for_claim(claim)
            if outgoing is not None:
                keywords = extract_keywords_simple(outgoing, max_keywords=5)
                search_terms = " ".join(keywords) if keywords else outgoing

                raw_results = await asyncio.wait_for(
                    effective_registry.query_all(
                        search_terms,
                        timeout=AUTHORITATIVE_VERIFY_QUERY_TIMEOUT,
                        raw_query=outgoing,
                        keywords=keywords,
                    ),
                    timeout=AUTHORITATIVE_VERIFY_QUERY_TIMEOUT + 1.0,
                )
                external_results = raw_results[:max_sources]
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('core.agents.hallucination.authoritative_verify', exc)
            query_error = type(exc).__name__
            logger.warning("Authoritative source query failed (non-blocking): %s", query_error)

    if not external_results:
        return {
            "authoritative_sources": [],
            "cross_validation": {},
            "claim_domain": domain,
            "domain_confidence": domain_detail["confidence"],
            "secondary_domains": domain_detail["secondary"],
            "evidence_summary": (
                f"Authoritative source query failed ({query_error}); no evidence gathered."
                if query_error
                else "No authoritative sources returned results."
            ),
            **({"query_error": query_error} if query_error else {}),
        }

    # Support / contradiction floors: NLI class probabilities (0-1), the same
    # thresholds the verdict path applies to these sources.
    entail_floor = config.NLI_ENTAILMENT_THRESHOLD
    contra_floor = config.NLI_CONTRADICTION_THRESHOLD

    # Step 2: NLI entailment between claim and each external result.
    # ExternalEvidence normalises the source dict; to_authoritative_dict emits
    # the legacy shape (source / content[:200] / source_url / nli_* / freshness).
    # ``nli_scored`` says whether the nli_* values were computed here. A source
    # whose scoring failed carries 0.0 placeholders (never a producer-supplied
    # value) and must not be read as scored-and-weak.
    scored_sources: list[dict[str, Any]] = []
    premises: list[str] = []  # the text each scored_sources entry was scored on
    for ext in external_results:
        content = ext.get("content", "")[:512]
        if not content:
            continue
        try:
            from core.utils.nli import nli_score_async

            nli = await nli_score_async(content, claim)
            entry = ExternalEvidence.from_mapping(
                ext,
                nli_entailment=float(nli["entailment"]),
                nli_contradiction=float(nli["contradiction"]),
            ).to_authoritative_dict()
            entry["nli_scored"] = True
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('core.agents.hallucination.authoritative_verify', exc)
            entry = ExternalEvidence.from_mapping(ext).to_authoritative_dict()
            entry.update(nli_entailment=0.0, nli_contradiction=0.0, nli_scored=False)
        scored_sources.append(entry)
        premises.append(content)

    scored = [s for s in scored_sources if s["nli_scored"]]

    # Step 3: Cross-validate the top KB result against the external source that
    # bears most on the claim; the first source returned can be unrelated to it.
    # Agreement / contradiction are NLI probabilities (0-1), KB text as premise.
    # A cross-validation that could not run says why instead of staying empty.
    cross_validation: dict[str, Any] = {}
    if kb_results:
        kb_text = (kb_results[0].get("content") or "")[:512]
        bearing = [
            i for i, s in enumerate(scored_sources)
            if s["nli_scored"]
            and (s["nli_entailment"] >= entail_floor or s["nli_contradiction"] >= contra_floor)
        ]
        if not kb_text:
            cross_validation = {"skipped": "KB result has no content"}
        elif not bearing:
            cross_validation = {"skipped": "no external source bears on the claim"}
        else:
            best = max(
                bearing,
                key=lambda i: max(
                    scored_sources[i]["nli_entailment"], scored_sources[i]["nli_contradiction"],
                ),
            )
            try:
                from core.utils.nli import nli_score_async

                cross_nli = await nli_score_async(kb_text, premises[best])
                cross_validation = {
                    "kb_vs_external_agreement": float(cross_nli["entailment"]),
                    "kb_vs_external_contradiction": float(cross_nli["contradiction"]),
                    "compared_source": scored_sources[best]["source"],
                }
            except Exception as exc:
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('core.agents.hallucination.authoritative_verify', exc)
                cross_validation = {"skipped": f"NLI scoring failed ({type(exc).__name__})"}

    # Step 4: Build evidence summary for the LLM verifier, from scored sources only.
    supporting = [s for s in scored if s["nli_entailment"] >= entail_floor]
    contradicting = [s for s in scored if s["nli_contradiction"] >= contra_floor]
    unscored = len(scored_sources) - len(scored)

    if supporting and contradicting:
        summary = (
            f"Authoritative sources disagree: {len(supporting)} support and "
            f"{len(contradicting)} contradict the claim."
        )
    elif supporting:
        summary = f"{len(supporting)} authoritative source(s) support the claim."
    elif contradicting:
        summary = f"{len(contradicting)} authoritative source(s) contradict the claim."
    elif scored:
        summary = f"{len(scored)} source(s) queried — no strong entailment or contradiction."
    elif scored_sources:
        summary = f"{len(scored_sources)} source(s) returned but none could be NLI-scored."
    else:
        summary = "Authoritative sources returned no content to assess."
    if scored and unscored:
        summary += f" {unscored} source(s) could not be NLI-scored."

    return {
        "authoritative_sources": scored_sources,
        "cross_validation": cross_validation,
        "claim_domain": domain,
        "domain_confidence": domain_detail["confidence"],
        "secondary_domains": domain_detail["secondary"],
        "evidence_summary": summary,
    }
