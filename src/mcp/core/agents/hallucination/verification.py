# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination detection — claim verification against KB and cross-model LLM.

Provides KB-based similarity verification, external cross-model verification,
numeric contradiction detection, and confidence calibration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx

import config
from core.agents.hallucination.extraction import _reclassify_recency
from core.agents.hallucination.patterns import (
    MEMORY_AUTHORITY_BOOST,
    MEMORY_TYPES,
    PERCENT_RE,
    YEAR_RE,
    _get_ext_verify_semaphore,
    _has_staleness_indicators,
    _is_complex_claim,
    _is_current_event_claim,
    _is_ignorance_admission,
    _is_recency_claim,
    _pick_verification_model,
)
from core.utils.circuit_breaker import CircuitOpenError, NonTransientError
from core.utils.claim_cache import cache_verdict, get_cached_verdict
from core.utils.llm_parsing import parse_llm_json


class CreditExhaustedError(NonTransientError):
    """Raised when the LLM provider returns 402 (payment required / credits exhausted).

    Inherits from NonTransientError so the circuit breaker does NOT count this
    as a failure -- 402 is permanent until the user adds credits, and opening
    the circuit would just add a 90s delay on top of an already-broken state.
    """

    def __init__(self, provider: str = "openrouter"):
        self.provider = provider
        super().__init__(f"{provider} credits exhausted (HTTP 402)")

logger = logging.getLogger("ai-companion.hallucination")


# ---------------------------------------------------------------------------
# System prompts for verification LLM calls
# ---------------------------------------------------------------------------

_EMPIRICAL_SOURCE_GUIDANCE = (
    "\n\nPrioritize these empirical source types (in order):\n"
    "1. Government data (.gov): CDC, BLS, Census Bureau, FBI UCR/NIBRS, DOJ, WHO, EPA\n"
    "2. Academic databases: PubMed, Google Scholar, JSTOR, peer-reviewed journals\n"
    "3. Official statistics portals: data.gov, FRED, World Bank Data, OECD\n"
    "4. Authoritative encyclopedic sources: Wikipedia (with citations), Britannica\n"
    "5. Reputable news with primary sourcing: Reuters, AP, verified reporting\n"
    "Cite the specific source and dataset when available."
)

_SYSTEM_DIRECT_VERIFICATION = (
    "You are a factual claim verifier. You are verifying a claim made by a "
    "different AI model. Your job is to independently assess accuracy — do not "
    "assume the claim is correct just because another AI generated it.\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "1-2 sentence explanation"}\n\n'
    "Rules:\n"
    "- \"supported\": The claim is factually accurate\n"
    "- \"refuted\": The claim contains clear factual errors\n"
    "- \"insufficient_info\": You cannot confidently verify or refute\n"
    "- Be honest about uncertainty — use \"insufficient_info\" when unsure\n"
    "- For \"refuted\" claims, briefly state what is wrong\n"
    "- confidence: 0.0 = no idea, 1.0 = certain"
)

_SYSTEM_CURRENT_EVENT_VERIFICATION = (
    "You are a factual claim verifier with access to real-time web search. "
    "You are verifying a claim made by a different AI model. Your job is to "
    "independently assess accuracy using web sources — do not assume the claim "
    "is correct just because another AI generated it.\n\n"
    "The current date is {current_date}. Any claim "
    "referencing events before this date should be evaluated based on whether "
    "those events have already occurred.\n\n"
    "Search the web for authoritative sources to confirm or refute the claim."
    f"{_EMPIRICAL_SOURCE_GUIDANCE}\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "1-2 sentence explanation with source reference"}\n\n'
    "Rules:\n"
    "- \"supported\": Web sources confirm the claim is accurate\n"
    "- \"refuted\": Web sources show the claim contains factual errors\n"
    "- \"insufficient_info\": Cannot find reliable sources to verify\n"
    "- Always cite the source in your reasoning (e.g., \"per CDC data...\", "
    "\"according to FBI UCR...\", \"per BLS statistics...\")\n"
    "- confidence: 0.0 = no sources found, 1.0 = multiple authoritative sources agree"
)

_SYSTEM_IGNORANCE_VERIFICATION = (
    "You are a factual claim verifier with access to real-time web search. "
    "An AI model has admitted it does not have information about a specific "
    "topic. Your job is to determine whether the information the model claims "
    "not to have actually exists in the real world.\n\n"
    "Do NOT evaluate whether the model is being honest about its limitations. "
    "Instead, search the web for authoritative sources about the UNDERLYING "
    "TOPIC — the facts, events, or information the model says it cannot "
    "provide."
    f"{_EMPIRICAL_SOURCE_GUIDANCE}\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "1-2 sentence explanation with source reference"}\n\n'
    "Rules:\n"
    "- \"supported\": The underlying information DOES exist — the model's "
    "response was outdated or incomplete\n"
    "- \"refuted\": The information genuinely does not exist or cannot be "
    "verified — the model was correct to say it lacks this information\n"
    "- \"insufficient_info\": Cannot find reliable sources to determine\n"
    "- Always cite sources in your reasoning\n"
    "- confidence: 0.0 = no sources found, 1.0 = multiple authoritative "
    "sources confirm"
)

_SYSTEM_EVASION_VERIFICATION = (
    "You are a factual claim verifier with access to real-time web search. "
    "A different AI model was asked a specific factual question but evaded "
    "answering with concrete data — instead giving hedging language, "
    "deflections, or generic disclaimers about complexity.\n\n"
    "Your job is to find and provide the actual factual answer using "
    "authoritative empirical sources. Search for the specific data the user "
    "requested. Do NOT hedge or deflect — provide concrete numbers, "
    "statistics, and facts with source citations."
    f"{_EMPIRICAL_SOURCE_GUIDANCE}\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "Concrete answer with source citations"}\n\n'
    "Rules:\n"
    "- \"supported\": The data exists and you found concrete answers — "
    "the model's evasion was unjustified\n"
    "- \"refuted\": The specific data genuinely does not exist or is "
    "legitimately impossible to determine\n"
    "- \"insufficient_info\": Cannot find authoritative sources for this "
    "specific question\n"
    "- Include specific numbers, percentages, or data points in your "
    "reasoning when available\n"
    "- confidence: 0.0 = no data found, 1.0 = authoritative data with "
    "clear answer"
)

_SYSTEM_RECENCY_VERIFICATION = (
    "You are a factual claim verifier with access to real-time web search. "
    "An AI model made a claim that may be based on outdated training data. "
    "Your job is to search for the MOST CURRENT data on this topic and "
    "determine whether the model's information is still accurate or has been "
    "superseded by newer data.\n\n"
    "The current date is {current_date}. Any claim "
    "referencing events before this date should be evaluated based on whether "
    "those events have already occurred.\n\n"
    "If the claim contains specific numbers, dates, or facts, search for "
    "the latest available data and compare. Report whether the model's "
    "information is current or outdated, citing the most recent source."
    f"{_EMPIRICAL_SOURCE_GUIDANCE}\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "1-2 sentence explanation comparing model data vs current data"}\n\n'
    "Rules:\n"
    "- \"supported\": The model's data is still current and accurate\n"
    "- \"refuted\": The model's data is outdated — newer data is available. "
    "State what the current data shows.\n"
    "- \"insufficient_info\": Cannot find current authoritative sources to compare\n"
    "- Always state the most recent data point and its source\n"
    "- confidence: 0.0 = no sources found, 1.0 = clear current data with source"
)

_SYSTEM_CITATION_VERIFICATION = (
    "You are a source verification specialist with access to real-time web search. "
    "An AI model cited a specific source (publication, study, report, or "
    "organization) in its response. Your job is to verify whether this cited "
    "source actually exists.\n\n"
    "Search the web for the exact source name. Check if it is a real "
    "publication, study, report, dataset, or organization.\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "1-2 sentence explanation"}\n\n'
    "Rules:\n"
    "- \"supported\": The cited source exists and is real\n"
    "- \"refuted\": The source appears to be fabricated — no evidence it exists\n"
    "- \"insufficient_info\": Cannot definitively confirm or deny existence\n"
    "- confidence: 0.0 = no info, 1.0 = source clearly exists (or clearly doesn't)"
)

_SYSTEM_CONSISTENCY_CHECK = (
    "You are a logical consistency analyzer. Given a list of claims from an "
    "AI model's latest response and (optionally) prior conversation context, "
    "identify any logical contradictions.\n\n"
    "Check for:\n"
    "1. Claims in the latest response that contradict EACH OTHER "
    "(inconsistent numbers, conflicting statements, logical non-sequiturs)\n"
    "2. Claims in the latest response that contradict statements from "
    "prior conversation turns\n\n"
    "Respond with ONLY a JSON array — no other text. Each element:\n"
    '{"claim_index": <int>, "contradiction": "<description>", '
    '"conflicting_claim_index": <int or null>, '
    '"type": "internal"|"history"}\n\n'
    "Rules:\n"
    "- claim_index: 0-based index of the claim in the current response\n"
    "- conflicting_claim_index: index of the other contradicting claim "
    "(null for history contradictions)\n"
    "- type: \"internal\" for within-response, \"history\" for cross-turn\n"
    "- Only flag CLEAR contradictions, not subtle differences or evolving context\n"
    "- Return [] if no contradictions found"
)


# ---------------------------------------------------------------------------
# Verdict inversion helpers
# ---------------------------------------------------------------------------

def _invert_ignorance_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Invert a verification verdict for an ignorance-admitting claim.

    When a model says "I don't know about X" and the verifier confirms X
    exists (verdict = "verified"), the model's response was factually
    inadequate — it should be marked as *unverified* (refuted in the UI).

    Conversely, if the verifier says the underlying facts don't exist
    (verdict = "unverified"), the model was correct to say it doesn't have
    that information — mark as *verified*.

    Confidence is preserved: high verifier confidence in the existence of
    the facts means high confidence in the refutation.
    """
    status = verdict["status"]
    reasoning = verdict.get("reason", "")

    if status == "verified":
        # Verifier confirms the underlying facts exist → model was wrong
        # to say it doesn't have the information (response was inadequate).
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification confirmed: ",
            "Cross-model verification confirmed",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "unverified",
            "reason": (
                f"Response was factually inadequate — the information exists: "
                f"{clean_reason}"
            ).rstrip(": "),
        }

    if status == "unverified":
        # Verifier says the underlying facts don't exist → model was
        # correct that it has no information about this topic.
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification found factual errors: ",
            "Cross-model verification found factual errors",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "verified",
            "confidence": max(verdict.get("confidence", 0.5), 0.7),
            "reason": (
                f"Model correctly identified lack of information: "
                f"{clean_reason}"
            ).rstrip(": "),
        }

    # uncertain / error — keep as-is
    return verdict


def _invert_evasion_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Invert a verification verdict for an evasion claim.

    When a model evades answering and Grok finds the actual data
    (verdict = "verified"/supported), the model's evasion was unjustified
    → mark as "unverified" (refuted in the UI).

    If Grok confirms the data genuinely doesn't exist ("unverified"/refuted),
    the model's caution was justified → mark as "verified".
    """
    status = verdict["status"]
    reasoning = verdict.get("reason", "")

    if status == "verified":
        # Data exists — model's evasion was unjustified
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification confirmed: ",
            "Cross-model verification confirmed",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "unverified",
            "reason": (
                f"Model evaded answering — data is available: {clean_reason}"
            ).rstrip(": "),
        }

    if status == "unverified":
        # Data genuinely unavailable — evasion was justified
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification found factual errors: ",
            "Cross-model verification found factual errors",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "verified",
            "confidence": max(verdict.get("confidence", 0.5), 0.7),
            "reason": (
                f"Model's caution was justified — data is unavailable: "
                f"{clean_reason}"
            ).rstrip(": "),
        }

    # uncertain / error — keep as-is
    return verdict


def _interpret_recency_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Interpret a verification verdict for a recency/staleness claim.

    Unlike ignorance inversion, recency verdicts map directly:
    - "supported" → model's data is still current → "verified"
    - "refuted" → model's data is outdated → "unverified" with current data
    - "uncertain" → keep as-is
    """
    status = verdict["status"]
    reasoning = verdict.get("reason", "")

    if status == "verified":
        # Model's data confirmed as current
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification confirmed: ",
            "Cross-model verification confirmed",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "verified",
            "reason": f"Data confirmed current: {clean_reason}".rstrip(": "),
        }

    if status == "unverified":
        # Model's data is outdated — newer data available
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification found factual errors: ",
            "Cross-model verification found factual errors",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "unverified",
            "reason": f"Outdated: {clean_reason}".rstrip(": "),
        }

    return verdict


# ---------------------------------------------------------------------------
# Numeric contradiction detection (zero LLM cost)
# ---------------------------------------------------------------------------

def _check_numeric_alignment(
    claim: str,
    top_result: dict[str, Any],
) -> float:
    """Check if specific numbers/dates/percentages in the claim match the source.

    Returns a small positive adjustment if numbers match, negative if they conflict.
    Returns 0.0 if no numbers to compare.

    This is the key defense against inverted-fact hallucinations where embedding
    similarity is high but the actual data is wrong (e.g. "released in 2021"
    vs source saying "released in 1991").
    """
    source_text = top_result.get("content", "")
    if not source_text:
        return 0.0

    claim_years = set(YEAR_RE.findall(claim))
    claim_pcts = set(PERCENT_RE.findall(claim))

    # No verifiable numbers in claim = nothing to check
    if not claim_years and not claim_pcts:
        return 0.0

    source_years = set(YEAR_RE.findall(source_text))
    source_pcts = set(PERCENT_RE.findall(source_text))

    matches = 0
    total_checks = 0

    # Check years
    for year in claim_years:
        total_checks += 1
        if year in source_years:
            matches += 1

    # Check percentages
    for pct in claim_pcts:
        total_checks += 1
        if pct in source_pcts:
            matches += 1

    if total_checks == 0:
        return 0.0

    match_ratio = matches / total_checks

    if match_ratio >= 0.8:
        return 0.03   # Numbers align well
    elif match_ratio <= 0.2 and total_checks >= 2:
        return -0.05  # Numbers conflict — likely inverted fact
    return 0.0


# ---------------------------------------------------------------------------
# Multi-result confidence calibration (zero LLM cost)
# ---------------------------------------------------------------------------

def _compute_adjusted_confidence(
    claim: str,
    top_results: list[dict[str, Any]],
    raw_similarity: float,
) -> float:
    """Adjust confidence based on multi-result triangulation and snippet analysis.

    Factors:
    1. Score spread: tight spread across top results = corroborating evidence.
       Large drop-off from #1 to #2 = isolated match (weaker).
    2. Domain diversity: results from multiple domains = stronger evidence.
    3. Numeric alignment: do the hard facts (years, percentages) match?
    4. Result count: fewer results = less confident.
    """
    adjustment = 0.0

    # Factor 1: Score spread analysis
    if len(top_results) >= 2:
        scores = [r.get("relevance", 0.0) for r in top_results]
        spread = scores[0] - scores[-1]
        if spread < 0.15:
            # Multiple results at similar scores = corroborating evidence
            adjustment += 0.03
        elif spread > 0.4:
            # Only one strong match, others are distant = weaker evidence
            adjustment -= 0.03

    # Factor 2: Domain diversity
    if len(top_results) >= 2:
        domains = {r.get("domain") for r in top_results if r.get("relevance", 0) > 0.3}
        if len(domains) > 1:
            adjustment += 0.02

    # Factor 3: Snippet-based number/date verification
    adjustment += _check_numeric_alignment(claim, top_results[0])

    # Factor 4: Result count penalty
    if len(top_results) == 1:
        adjustment -= 0.02

    return max(0.0, min(1.0, raw_similarity + adjustment))


def _build_verification_details(
    claim: str,
    top_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build verification detail metadata for transparency and analytics."""
    scores = [r.get("relevance", 0.0) for r in top_results]
    domains = [r.get("domain", "") for r in top_results]

    details: dict[str, Any] = {
        "result_count": len(top_results),
        "top_scores": [round(s, 3) for s in scores],
        "domains_found": list(set(d for d in domains if d)),
        "score_spread": round(scores[0] - scores[-1], 3) if len(scores) > 1 else 0.0,
    }

    # Check for numeric alignment
    snippet_adj = _check_numeric_alignment(claim, top_results[0]) if top_results else 0.0
    if snippet_adj != 0.0:
        details["numeric_alignment"] = "match" if snippet_adj > 0 else "conflict"

    # Generate reason string based on analysis
    reasons = []
    if details["score_spread"] > 0.4:
        reasons.append("isolated match (large score drop-off)")
    if len(set(domains)) > 1 and all(s > 0.3 for s in scores[:2]):
        reasons.append("cross-domain corroboration")
    if snippet_adj < 0:
        reasons.append("numeric values conflict with source")
    if any(r.get("memory_source") for r in top_results[:1]):
        reasons.append("verified against user memory")
    if len(top_results) == 1:
        reasons.append("single result only")

    if reasons:
        details["reason"] = "; ".join(reasons)

    return details


# ---------------------------------------------------------------------------
# Memory-aware verification queries
# ---------------------------------------------------------------------------

async def _query_memories(
    claim: str,
    chroma_client,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    """Query the conversations collection for matching user-confirmed memories.

    Filters to memory_type artifacts to avoid matching feedback-ingested
    LLM responses (which would cause circular self-verification).
    """
    try:
        collection = chroma_client.get_collection(
            name=config.collection_name("conversations")
        )
        results = collection.query(
            query_texts=[claim],
            n_results=top_k,
            where={"memory_type": {"$in": MEMORY_TYPES}},
            include=["documents", "metadatas", "distances"],
        )

        formatted = []
        if results["ids"] and results["ids"][0]:
            for i, _chunk_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 1.0
                relevance = max(0.0, min(1.0, 1.0 - distance))
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                formatted.append({
                    "relevance": round(relevance, 4),
                    "artifact_id": metadata.get("artifact_id", ""),
                    "filename": metadata.get("filename", ""),
                    "domain": "conversations",
                    "content": results["documents"][0][i] if results["documents"] else "",
                    "memory_type": metadata.get("memory_type", ""),
                    "memory_source": True,
                })
        return formatted
    except Exception as e:
        logger.debug("Memory query failed (non-blocking): %s", e)
        return []


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------

def _parse_verification_verdict(raw: str) -> dict[str, Any]:
    """Parse structured JSON verdict from a direct verification model response.

    Expected format: {"verdict": "supported"|"refuted"|"insufficient_info",
                      "confidence": 0.0-1.0, "reasoning": "..."}

    Falls back to heuristic parsing if JSON is malformed.
    """
    if not raw or not raw.strip():
        return {
            "status": "uncertain",
            "confidence": 0.3,
            "reason": "Empty verification response",
        }

    # Try JSON parsing (handles markdown-wrapped ```json blocks too)
    try:
        parsed = parse_llm_json(raw)
    except (json.JSONDecodeError, ValueError, KeyError):
        parsed = None
    if isinstance(parsed, dict) and "verdict" in parsed:
        verdict = str(parsed["verdict"]).lower().strip()
        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(parsed.get("reasoning", ""))

        if verdict == "supported" and confidence >= 0.5:
            status = "verified"
        elif verdict == "refuted":
            status = "unverified"
            # Refuted claims get low confidence even if model says high
            confidence = min(confidence, 0.35)
        else:
            # insufficient_info, unrecognized, or low-confidence supported
            # → uncertain (neutral).  Unassessable claims get a truly neutral
            # score so they don't drag down the overall confidence average.
            status = "uncertain"
            confidence = 0.5

        reason_prefix = {
            "verified": "Cross-model verification confirmed",
            "unverified": "Cross-model verification found factual errors",
            "uncertain": "Claim not independently verifiable",
        }[status]

        reason = f"{reason_prefix}: {reasoning}" if reasoning else reason_prefix

        return {
            "status": status,
            "confidence": round(confidence, 3),
            "reason": reason,
        }

    # Fallback: model returned free text instead of JSON —
    # look for strong signal words as a last resort
    lower = raw.lower()
    if any(w in lower for w in ("incorrect", "false", "wrong", "inaccurate", "not true")):
        return {
            "status": "unverified",
            "confidence": 0.3,
            "reason": "Cross-model verification found inconsistencies (non-JSON response)",
        }
    if any(w in lower for w in ("correct", "accurate", "true", "confirmed", "yes,")):
        return {
            "status": "verified",
            "confidence": 0.65,
            "reason": "Cross-model verification confirmed (non-JSON response)",
        }

    return {
        "status": "uncertain",
        "confidence": 0.5,
        "reason": "Claim not independently verifiable (unparseable response)",
    }


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------

async def _llm_call_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    max_attempts: int | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    """POST to an LLM endpoint with exponential backoff on 429 responses."""
    if max_attempts is None:
        max_attempts = config.EXTERNAL_VERIFY_RETRY_ATTEMPTS
    base_delay = config.EXTERNAL_VERIFY_RETRY_BASE_DELAY

    post_kwargs: dict = {"json": payload}
    if timeout is not None:
        post_kwargs["timeout"] = timeout

    for attempt in range(max_attempts):
        resp = await client.post(url, **post_kwargs)
        # 402 = payment required / credits exhausted — not transient, don't retry
        if resp.status_code == 402:
            raise CreditExhaustedError("openrouter")
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        # 429 — wait with exponential backoff, respect Retry-After if present
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = base_delay * (2 ** attempt)
        else:
            delay = base_delay * (2 ** attempt)
        logger.info(
            "429 rate-limited (attempt %d/%d), retrying in %.1fs",
            attempt + 1, max_attempts, delay,
        )
        await asyncio.sleep(delay)

    # Exhausted retries — raise the last 429 so the caller handles it
    resp.raise_for_status()
    return resp  # unreachable, but keeps mypy happy


# ---------------------------------------------------------------------------
# External (Cross-Model) Verification — Direct Structured Verdict
# ---------------------------------------------------------------------------

async def _verify_claim_externally(
    claim: str,
    generating_model: str | None = None,
    force_web_search: bool = False,
    streaming: bool = False,
    expert_mode: bool = False,
    fast_mode: bool = False,
    claim_context: str | None = None,
    response_context: str | None = None,
) -> dict[str, Any]:
    """Direct structured cross-model verification for a single claim.

    Strategy: send the claim directly to a model from a *different family*
    than the generator and ask for a structured JSON verdict.  Cross-model
    diversity prevents correlated hallucinations without the need for
    anti-anchoring (hiding the claim via a neutral question).

    Current-event claims are routed to a web-search-enabled model (Grok +
    live web search via the OpenRouter ``:online`` suffix) for real-time
    verification against authoritative web sources.

    **Ignorance-admission claims** (e.g. "I don't have information about X")
    are detected and handled specially: the verification checks whether the
    underlying facts actually exist, and the verdict is inverted — if the
    facts exist, the model's response was inadequate (marked as refuted).

    When ``force_web_search`` is True, the claim is sent directly to the
    web-search model regardless of current-event detection.  This is used
    by the staleness escalation path when a static model admits stale
    knowledge in its reasoning.

    When ``streaming`` is True, the function uses fewer LLM retries and
    skips the staleness-escalation recursive call to avoid compounding
    delays in the SSE streaming path.

    When ``expert_mode`` is True, the expert-tier model (Grok 4) is used
    for all claim types, overriding pool-based selection.  For claims
    requiring web search, the ``:online`` suffix is appended.

    When ``fast_mode`` is True, the function uses the fastest available
    model (GPT-4o-mini) with 1 retry, skipping the multi-model fallback
    chain and staleness escalation.  Used for recency claims that don't
    specifically need current web data.

    Pipeline:
    1. Check feature flag (early return if disabled)
    2. Detect ignorance-admission claims (always → web search)
    3. Detect if claim is about current events (or forced web search)
    4. Pick verification model — web-search model for current/ignorance,
       pool-based cross-model for static claims
    5. Send claim with appropriate system/user prompt
    6. Parse JSON verdict → {status, confidence, reason}
    7. For ignorance claims, invert verdict (supported → refuted)
    8. Extract source URLs from OpenRouter annotations (web search)
    9. Detect staleness in reasoning → escalate to web search if needed

    A module-level semaphore limits concurrency as defense-in-depth.
    """
    if not config.ENABLE_EXTERNAL_VERIFICATION:
        return {
            "status": "uncertain",
            "confidence": 0.3,
            "reason": "External verification disabled",
            "verification_method": "none",
            "source_urls": [],
        }

    # Detect evasion claims (synthesized by _detect_evasion)
    is_evasion = claim.startswith("[EVASION]")

    # Detect citation verification claims (synthesized by _extract_citation_claims)
    is_citation = claim.startswith("[CITATION]")

    # Detect recency/staleness claims (model hedged about its training data)
    # Must check before ignorance — recency claims are also ignorance-adjacent
    # but need different handling (compare data, not just check existence).
    # Also catch date-based claims via _reclassify_recency (e.g. "2024
    # elections are upcoming") that the stale-knowledge pattern misses.
    is_recency = (
        not is_evasion and not is_citation
        and (_is_recency_claim(claim) or _reclassify_recency(claim, "factual") == "recency")
    )

    # Detect ignorance-admitting claims ("I don't have info about X")
    # These always use web search and get inverted verdicts.
    # Exclude recency claims — they have separate handling.
    is_ignorance = (
        not is_evasion and not is_citation and not is_recency
        and _is_ignorance_admission(claim)
    )

    # Determine if the claim needs web-search verification.
    # Ignorance/evasion/recency/citation claims always go to web search.
    is_current_event = (
        force_web_search or is_evasion or is_ignorance
        or is_recency or is_citation or _is_current_event_claim(claim)
    )

    if is_current_event:
        verify_model = config.VERIFICATION_CURRENT_EVENT_MODEL
        if is_evasion:
            system_prompt = _SYSTEM_EVASION_VERIFICATION
        elif is_citation:
            system_prompt = _SYSTEM_CITATION_VERIFICATION
        elif is_recency:
            system_prompt = _SYSTEM_RECENCY_VERIFICATION
        elif is_ignorance:
            system_prompt = _SYSTEM_IGNORANCE_VERIFICATION
        else:
            system_prompt = _SYSTEM_CURRENT_EVENT_VERIFICATION
        verification_method = "web_search"
        # Inject current date into system prompt (was baked in at import time — stale after midnight)
        system_prompt = system_prompt.replace("{current_date}", datetime.now().strftime("%Y-%m-%d"))
    else:
        # Complex claims (causal, comparative, multi-hop) use a stronger
        # model for more reliable verdicts.  Simple factual claims use the
        # lightweight cross-model pool for cost efficiency.
        if _is_complex_claim(claim):
            verify_model = config.VERIFICATION_COMPLEX_MODEL
            verification_method = "cross_model_complex"
        else:
            verify_model = _pick_verification_model(generating_model)
            verification_method = "cross_model"
        system_prompt = _SYSTEM_DIRECT_VERIFICATION

    # Expert mode: override model selection with the expert-tier model
    if expert_mode:
        if is_current_event:
            # Append :online for web-search-capable verification
            verify_model = config.VERIFICATION_EXPERT_MODEL + ":online"
        else:
            verify_model = config.VERIFICATION_EXPERT_MODEL
        logger.debug("Expert mode: using %s for claim verification", verify_model)

    # Fast mode: use cheapest/fastest model, 1 retry, skip staleness escalation
    if fast_mode and not expert_mode:
        verify_model = config.VERIFICATION_MODEL  # GPT-4o-mini
        verification_method = "cross_model_fast"
        logger.debug("Fast mode: using %s for claim verification", verify_model)

    sem = _get_ext_verify_semaphore()

    async with sem:
        try:
            # Include generating model context so the verifier knows it's
            # checking another AI's output (prevents self-confirmation bias)
            model_context = (
                f"\n\nThis claim was generated by {generating_model}."
                if generating_model else ""
            )

            # JSON format is already specified in the system prompt —
            # appending it again to user prompts wastes ~30 tokens per call.
            _json_response_fmt = ""

            # Prepend topic context when available so ambiguous claims
            # like "It is 330 meters tall" can be resolved.
            context_line = (
                f"\n\nContext: this claim is from a response about: {response_context}"
                if response_context else ""
            )

            if is_evasion:
                # Extract the user's original question from the evasion claim
                q_match = re.search(r'The user asked: "(.+?)"', claim)
                user_question = q_match.group(1) if q_match else claim
                user_prompt = (
                    f"A user asked an AI model this question: \"{user_question}\"\n\n"
                    f"The model evaded answering with concrete data, instead "
                    f"giving hedging language and deflections. Your job is to "
                    f"find and provide the actual factual answer using "
                    f"authoritative empirical sources."
                    f"{model_context}\n\n{_json_response_fmt}"
                )
            elif is_citation:
                # Strip [CITATION] prefix for the verification prompt
                citation_text = claim.removeprefix("[CITATION] ").strip()
                user_prompt = (
                    f"An AI model cited this source: \"{citation_text}\"\n\n"
                    f"Verify whether this source, publication, organization, "
                    f"or study actually exists and is a real, authoritative "
                    f"reference. Search for it by name."
                    f"{model_context}\n\n{_json_response_fmt}"
                )
            elif is_recency:
                user_prompt = (
                    f"An AI model made this claim: \"{claim}\"\n\n"
                    f"The model appears to be stating information that may be "
                    f"based on outdated training data. Search for the MOST "
                    f"CURRENT data on this topic and determine whether the "
                    f"model's information is still accurate or has been "
                    f"superseded by newer data. If the claim contains specific "
                    f"numbers, dates, or facts, find the latest available data "
                    f"and compare."
                    f"{context_line}{model_context}\n\n{_json_response_fmt}"
                )
            elif is_ignorance:
                # Reframed prompt: check underlying facts, not the model's honesty
                user_prompt = (
                    f"An AI model said: \"{claim}\"\n\n"
                    f"The model is admitting it lacks knowledge about a topic. "
                    f"Do NOT evaluate whether the model is honest about its "
                    f"limitations. Instead, search for and verify whether the "
                    f"underlying facts, events, or information actually exist."
                    f"{context_line}{model_context}\n\n{_json_response_fmt}"
                )
            else:
                # Include surrounding text so the verifier understands the framing
                # (e.g., a wavelength table where each row is a separate claim)
                ctx_block = (
                    f"\n\nSurrounding text from the response:\n\"{claim_context}\"\n"
                    if claim_context else ""
                )
                user_prompt = (
                    f"Assess this claim for factual accuracy:\n\n"
                    f"\"{claim}\"{ctx_block}{context_line}{model_context}\n\n{_json_response_fmt}"
                )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Increase timeout for web-search calls — they take longer
            timeout = config.BIFROST_TIMEOUT * 2 if is_current_event else config.BIFROST_TIMEOUT

            from core.utils.llm_client import call_llm_raw
            data = await call_llm_raw(
                messages,
                model=verify_model,
                temperature=config.EXTERNAL_VERIFY_TEMPERATURE,
                max_tokens=config.EXTERNAL_VERIFY_MAX_TOKENS,
                timeout=timeout,
                breaker_name="bifrost-verify",
            )
            raw_message = data["choices"][0]["message"]
            raw_answer = raw_message.get("content", "").strip()

            # Extract source URLs from OpenRouter URL citation annotations
            # (present when using :online suffix models like Grok)
            annotations = raw_message.get("annotations", [])
            source_urls: list[str] = []
            seen_urls: set = set()
            for a in annotations:
                if a.get("type") == "url_citation":
                    url_str = a.get("url_citation", {}).get("url", "")
                    if url_str and url_str not in seen_urls:
                        source_urls.append(url_str)
                        seen_urls.add(url_str)

            # Parse structured verdict directly — no regex comparison needed
            verdict = _parse_verification_verdict(raw_answer)

            # --- Ignorance-admission verdict inversion ---
            if is_ignorance:
                verdict = _invert_ignorance_verdict(verdict)
                logger.info(
                    "Ignorance-admission claim detected, verdict inverted: "
                    "'%s...' → %s",
                    claim[:50],
                    verdict["status"],
                )

            # --- Evasion verdict inversion ---
            if is_evasion:
                verdict = _invert_evasion_verdict(verdict)
                logger.info(
                    "Evasion claim detected, verdict inverted: "
                    "'%s...' → %s",
                    claim[:50],
                    verdict["status"],
                )

            # --- Recency verdict (direct mapping, no inversion) ---
            if is_recency:
                verdict = _interpret_recency_verdict(verdict)
                logger.info(
                    "Recency claim detected, verdict mapped: "
                    "'%s...' → %s",
                    claim[:50],
                    verdict["status"],
                )

            # --- Citation verification (direct mapping) ---
            if is_citation:
                verdict = _interpret_recency_verdict(verdict)
                logger.info(
                    "Citation claim verified: '%s...' → %s",
                    claim[:50],
                    verdict["status"],
                )

            # --- Staleness escalation ---
            if (
                not force_web_search
                and not is_ignorance
                and not is_current_event
                and not streaming
                and not fast_mode
                and _is_current_event_claim(claim)  # re-check with broader lens
                and verdict["status"] in ("verified", "uncertain")
                and _has_staleness_indicators(raw_answer)
            ):
                logger.info(
                    "Staleness detected in verification of '%s...' — "
                    "escalating to web search",
                    claim[:50],
                )
                return await _verify_claim_externally(
                    claim, generating_model, force_web_search=True,
                    response_context=response_context,
                )

            return {
                **verdict,
                "verification_method": verification_method,
                "verification_model": verify_model,
                "verification_answer": raw_answer,
                "source_urls": source_urls,
            }

        except CreditExhaustedError as credit_err:
            logger.warning(
                "Provider credits exhausted (402) for '%s...': %s",
                claim[:50], credit_err,
            )
            return {
                "status": "skipped",
                "confidence": 0,
                "reason": "Provider credits exhausted",
                "verification_method": "credit_exhausted",
                "source_urls": [],
                "credit_exhausted": True,
            }
        except CircuitOpenError:
            logger.warning("Bifrost verify circuit open for '%s...'", claim[:50])
            return {
                "status": "uncertain",
                "confidence": 0.3,
                "reason": "Verification service temporarily unavailable",
                "verification_method": "circuit_open",
                "source_urls": [],
            }
        except Exception as e:
            logger.warning("External verification failed for '%s...': %s", claim[:50], e)
            return {
                "status": "uncertain",
                "confidence": 0.3,
                "reason": f"External verification failed: {e}",
                "verification_method": f"{verification_method}_failed",
                "source_urls": [],
            }


# ---------------------------------------------------------------------------
# KB source field extraction
# ---------------------------------------------------------------------------

def _kb_source_fields(top_result: dict[str, Any] | None) -> dict[str, Any]:
    """Extract KB source metadata for inclusion in verification results."""
    if not top_result:
        return {}
    return {
        "source_artifact_id": top_result.get("artifact_id", ""),
        "source_filename": top_result.get("filename", ""),
        "source_domain": top_result.get("domain", ""),
        "source_snippet": top_result.get("content", "")[:200],
    }


# ---------------------------------------------------------------------------
# Batch external verification (same-model claim grouping)
# ---------------------------------------------------------------------------

_SYSTEM_BATCH_VERIFICATION = (
    "You are a fact-checking engine. Verify each claim for factual accuracy. "
    "Respond with ONLY a JSON array, one object per claim in order. "
    "Each: {\"claim_index\": N, \"verdict\": \"supported\"|\"refuted\"|\"insufficient_info\", "
    "\"confidence\": 0.0-1.0, \"reasoning\": \"brief explanation\"}"
)

_BATCH_JSON_FMT = ""  # Schema is in the system prompt — no need to repeat


async def verify_claims_batch_external(
    claims: list[tuple[int, str]],
    model: str,
    response_context: str | None = None,
    timeout: float | None = None,
) -> dict[int, dict[str, Any]]:
    """Verify multiple claims in a single LLM call to the same model.

    Args:
        claims: List of (original_index, claim_text) tuples.
        model: The model to use for all claims in this batch.
        response_context: Topic context for ambiguous claims.
        timeout: Per-batch timeout (default: BIFROST_TIMEOUT * 3).

    Returns:
        Dict mapping original_index → verdict dict with keys:
        status, similarity, reason, verification_method, verification_model.
    """
    if not claims:
        return {}

    if timeout is None:
        timeout = config.BIFROST_TIMEOUT * 3

    context_line = (
        f"\nContext: these claims are from a response about: {response_context}\n"
        if response_context else ""
    )

    claims_block = "\n".join(
        f"  [{i}] \"{text}\"" for i, (_, text) in enumerate(claims)
    )
    user_prompt = (
        f"Verify each of the following {len(claims)} claims for factual accuracy:"
        f"{context_line}\n\n{claims_block}{_BATCH_JSON_FMT}"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_BATCH_VERIFICATION},
        {"role": "user", "content": user_prompt},
    ]

    results: dict[int, dict[str, Any]] = {}

    try:
        from core.utils.llm_client import call_llm_raw
        data = await call_llm_raw(
            messages,
            model=model,
            temperature=0.1,
            max_tokens=min(100 * len(claims), 800),  # ~100 tokens per verdict
            timeout=timeout,
            breaker_name="bifrost-verify",
        )
        raw_answer = data["choices"][0]["message"].get("content", "").strip()

        # Extract source URLs from annotations (web search models)
        annotations = data["choices"][0]["message"].get("annotations", [])
        source_urls: list[str] = []
        seen_urls: set = set()
        for a in annotations:
            if a.get("type") == "url_citation":
                url_str = a.get("url_citation", {}).get("url", "")
                if url_str and url_str not in seen_urls:
                    source_urls.append(url_str)
                    seen_urls.add(url_str)

        # Parse the JSON array response
        from core.utils.llm_parsing import parse_llm_json
        parsed = parse_llm_json(raw_answer)
        if isinstance(parsed, dict):
            parsed = parsed.get("results", parsed.get("claims", []))
        if not isinstance(parsed, list):
            logger.warning("Batch verification returned non-array: %s", type(parsed).__name__)
            return results

        for item in parsed:
            if not isinstance(item, dict):
                continue
            batch_idx = item.get("claim_index", -1)
            if not isinstance(batch_idx, int) or batch_idx < 0 or batch_idx >= len(claims):
                continue
            original_idx = claims[batch_idx][0]
            # Reuse the same parsing logic as single-claim verification
            verdict_obj = _parse_verification_verdict(
                json.dumps(item) if isinstance(item, dict) else str(item)
            )
            status = verdict_obj.get("status", "uncertain")
            confidence = verdict_obj.get("similarity", 0.5)
            reason = verdict_obj.get("reason", "")[:200]

            results[original_idx] = {
                "claim": claims[batch_idx][1],
                "status": status,
                "similarity": confidence,
                "reason": reason,
                "verification_method": "web_search" if ":online" in model else "cross_model",
                "verification_model": model,
                "source_urls": source_urls[:3] if source_urls else [],
            }

        logger.info(
            "Batch verification: %d/%d claims resolved via %s",
            len(results), len(claims), model,
        )
    except Exception as exc:
        logger.warning("Batch verification failed (%s), claims will fall back to individual", exc)

    return results


# ---------------------------------------------------------------------------
# Main claim verification
# ---------------------------------------------------------------------------

async def verify_claim(
    claim: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: float | None = None,
    model: str | None = None,
    streaming: bool = False,
    expert_mode: bool = False,
    source_artifact_ids: list[str] | None = None,
    response_context: str | None = None,
    claim_context: str | None = None,
) -> dict[str, Any]:
    """Verify a single claim against the knowledge base and user memories.

    When ``claim_context`` is provided, the surrounding text from the original
    response is included in the verification prompt so the verifier understands
    the framing (e.g., a table listing wavelengths for all colors).

    When ``response_context`` is provided, it is prepended to external
    verification prompts so the verifier knows the topic being discussed
    (e.g. "The response is about the Eiffel Tower").  This prevents
    ambiguous claims like "It is 330 meters tall" from returning
    uncertain due to missing subject context.

    Uses multi-result triangulation, numeric contradiction detection,
    and memory authority to produce calibrated confidence scores.

    Falls back to cross-model external verification when the KB cannot
    provide a definitive answer:
    - Fallback 1: No KB results at all
    - Fallback 2: Very low KB similarity (< ext_kb_threshold)
    - Fallback 3: KB says "unverified" — external may provide a verdict
    - Fallback 4: KB says "uncertain" — external may resolve ambiguity

    Only KB-verified claims (similarity >= threshold) skip external
    verification, since the KB provides strong positive evidence.

    When ``streaming`` is True, the function limits external verification
    to a single call per fallback path (skipping fallback 4's secondary
    web search escalation) to avoid compounding delays in the SSE path.

    When ``expert_mode`` is True, all external verification calls use the
    expert-tier model (Grok 4) instead of the default model pool.
    """
    from core.agents.query_agent import lightweight_kb_query

    if threshold is None:
        threshold = config.HALLUCINATION_THRESHOLD
    unverified_threshold = config.HALLUCINATION_UNVERIFIED_THRESHOLD
    ext_kb_threshold = config.EXTERNAL_VERIFY_KB_THRESHOLD

    # --- Fact-level cache: skip re-verification for previously seen claims ---
    cached = await get_cached_verdict(redis_client, claim)
    if cached and cached.get("status") in ("verified", "unverified"):
        logger.info("Claim cache hit: '%s' -> %s", claim[:50], cached["status"])
        return {
            **cached,
            "claim": claim,
            "cached": True,
        }

    async def _cache_result(result: dict[str, Any]) -> dict[str, Any]:
        """Cache the verdict (fire-and-forget) and return the result unchanged."""
        if result.get("status") in ("verified", "unverified"):
            await cache_verdict(redis_client, claim, result, response_context=response_context)
        return result

    try:
        # Exclude 'conversations' domain from general KB query to avoid
        # self-verification against feedback-ingested LLM responses.
        verification_domains = [d for d in config.DOMAINS if d != "conversations"]
        # Use lightweight retrieval (vector + BM25 hybrid only) — skips graph
        # expansion, cross-encoder, quality boost, MMR, and context assembly
        # for significantly faster per-claim verification.
        kb_results = await lightweight_kb_query(
            query=claim,
            domains=verification_domains,
            top_k=5,
            chroma_client=chroma_client,
        )

        # Also query user-confirmed memories (filtered by memory_type)
        memory_results = await _query_memories(claim, chroma_client, top_k=2)

        # Merge KB results with memory results
        all_results = list(kb_results)
        for mr in memory_results:
            # Preserve raw relevance for escalation decisions
            raw_rel = mr["relevance"]
            mr["_raw_relevance"] = raw_rel
            # Memories get an authority boost (user-confirmed content)
            mr["relevance"] = min(1.0, round(raw_rel + MEMORY_AUTHORITY_BOOST, 4))
            all_results.append(mr)

        # Filter out results below verification relevance threshold
        all_results = [
            r for r in all_results
            if r.get("relevance", 0) >= config.VERIFICATION_MIN_RELEVANCE
        ]

        # --- Anti-circularity: penalise KB results that were injected into
        # the LLM prompt.  These cannot independently verify a claim because
        # the response was *derived* from them — matching is expected.
        if source_artifact_ids:
            _src_set = set(source_artifact_ids)
            for r in all_results:
                aid = r.get("artifact_id", "")
                if aid and aid in _src_set:
                    original_rel = r.get("relevance", 0.0)
                    r["_circular"] = True
                    r["relevance"] = max(0.0, round(original_rel - 0.3, 4))
                    logger.info(
                        "Anti-circular penalty: artifact=%s relevance %.3f → %.3f",
                        aid[:8], original_rel, r["relevance"],
                    )

        # Sort by relevance descending
        all_results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)

        # --- Heuristic sanity filter: drop KB results with low term overlap ---
        # Vector similarity can return false matches (e.g., a cabin project doc
        # matching a claim about light wavelengths). Free check — regex only.
        claim_lower = claim.lower()
        claim_terms = set(re.findall(r"\b[a-z]{4,}\b|\b\d[\d.,%]+\b", claim_lower))
        if claim_terms:
            filtered: list[dict[str, Any]] = []
            for r in all_results:
                src_text = r.get("content", "")[:300].lower()
                src_terms = set(re.findall(r"\b[a-z]{4,}\b|\b\d[\d.,%]+\b", src_text))
                overlap = len(claim_terms & src_terms) / len(claim_terms)
                if overlap >= 0.25:
                    filtered.append(r)
                else:
                    logger.debug(
                        "KB result filtered (%.0f%% term overlap): '%s…' vs claim '%s…'",
                        overlap * 100, src_text[:40], claim[:40],
                    )
            all_results = filtered

        # --- Fallback 1: No KB results at all → try external verification ---
        # Only force web search for claims that genuinely need current data.
        # Historical/established facts (pre-2024) can be verified via cross-model.
        if not all_results:
            needs_web = _is_current_event_claim(claim) or _is_recency_claim(claim)
            ext_result = await _verify_claim_externally(
                claim, model, force_web_search=needs_web, streaming=streaming,
                expert_mode=expert_mode, response_context=response_context, claim_context=claim_context,
            )
            return await _cache_result({
                "claim": claim,
                "status": ext_result["status"],
                "similarity": ext_result["confidence"],
                "reason": ext_result["reason"],
                "verification_method": ext_result.get("verification_method", "none"),
                "verification_model": ext_result.get("verification_model"),
                "source_urls": ext_result.get("source_urls", []),
                **({"credit_exhausted": True} if ext_result.get("credit_exhausted") else {}),
            })

        top_results = all_results[:3]
        top_result = top_results[0]
        raw_similarity = top_result.get("relevance", 0.0)
        # Use pre-boost relevance for escalation if this is a memory result,
        # so the +0.15 authority boost doesn't mask low KB evidence.
        escalation_similarity = top_result.get("_raw_relevance", raw_similarity)

        # --- Anti-circularity escalation: if ALL top results are circular
        # (derived from the same KB artifacts injected into the LLM prompt),
        # the KB cannot independently verify the claim — escalate externally.
        all_circular = source_artifact_ids and all(
            r.get("_circular") for r in top_results
        )
        if all_circular:
            logger.info(
                "All top KB results are circular for claim '%s…' — escalating to external",
                claim[:50],
            )
            ext_result = await _verify_claim_externally(
                claim, model, streaming=streaming,
                expert_mode=expert_mode, response_context=response_context, claim_context=claim_context,
            )
            return await _cache_result({
                "claim": claim,
                "status": ext_result["status"],
                "similarity": ext_result["confidence"],
                "reason": ext_result["reason"],
                "verification_method": ext_result.get("verification_method", "none"),
                "verification_model": ext_result.get("verification_model"),
                "source_urls": ext_result.get("source_urls", []),
                "circular_source": True,
                **({"credit_exhausted": True} if ext_result.get("credit_exhausted") else {}),
            })

        # --- Fallback 2: Very low KB similarity → try external verification ---
        if escalation_similarity < ext_kb_threshold:
            ext_result = await _verify_claim_externally(
                claim, model, streaming=streaming,
                expert_mode=expert_mode, response_context=response_context, claim_context=claim_context,
            )
            # Use external result if it provides a stronger signal than KB
            if ext_result["confidence"] > raw_similarity:
                return await _cache_result({
                    "claim": claim,
                    "status": ext_result["status"],
                    "similarity": ext_result["confidence"],
                    "reason": ext_result["reason"],
                    "verification_method": ext_result.get("verification_method", "none"),
                    "verification_model": ext_result.get("verification_model"),
                    "source_urls": ext_result.get("source_urls", []),
                    **({"credit_exhausted": True} if ext_result.get("credit_exhausted") else {}),
                })

        # Apply multi-result confidence calibration
        similarity = _compute_adjusted_confidence(claim, top_results, raw_similarity)
        details = _build_verification_details(claim, top_results)

        if similarity >= threshold:
            # Spurious matches already filtered by the term-overlap check above.
            return await _cache_result({
                "claim": claim,
                "status": "verified",
                "similarity": round(similarity, 3),
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "memory_source": bool(top_result.get("memory_source")),
                "verification_details": details,
                "verification_method": "kb",
                **({"circular_source": True} if top_result.get("_circular") else {}),
            })
        elif similarity < unverified_threshold:
            # --- Fallback 3: KB says "unverified" → try external ---
            ext_result = await _verify_claim_externally(
                claim, model, streaming=streaming,
                expert_mode=expert_mode, response_context=response_context, claim_context=claim_context,
            )
            if ext_result.get("status") in ("verified", "unverified"):
                return await _cache_result({
                    "claim": claim,
                    "status": ext_result["status"],
                    "similarity": ext_result["confidence"],
                    "reason": ext_result["reason"],
                    "verification_method": ext_result.get("verification_method", "none"),
                    "verification_model": ext_result.get("verification_model"),
                    "source_urls": ext_result.get("source_urls", []),
                    **({"credit_exhausted": True} if ext_result.get("credit_exhausted") else {}),
                })
            return await _cache_result({
                "claim": claim,
                "status": "unverified",
                "similarity": round(similarity, 3),
                "reason": details.get("reason", "Low similarity to any KB content"),
                "verification_details": details,
                "verification_method": "kb",
                **_kb_source_fields(top_result),
            })
        else:
            # --- Fallback 4: KB says "uncertain" → try external for a
            # definitive answer before falling back to KB-only uncertain ---
            ext_result = await _verify_claim_externally(
                claim, model, streaming=streaming,
                expert_mode=expert_mode, response_context=response_context, claim_context=claim_context,
            )
            if ext_result.get("status") in ("verified", "unverified"):
                return await _cache_result({
                    "claim": claim,
                    "status": ext_result["status"],
                    "similarity": ext_result["confidence"],
                    "reason": ext_result["reason"],
                    "verification_method": ext_result.get("verification_method", "none"),
                    "verification_model": ext_result.get("verification_model"),
                    "source_urls": ext_result.get("source_urls", []),
                    **({"credit_exhausted": True} if ext_result.get("credit_exhausted") else {}),
                })
            # External also uncertain — try web search as final escalation.
            # In streaming mode, skip this second external call to avoid
            # compounding delays (each call can take 20-40s + retries).
            if (
                not streaming
                and ext_result.get("verification_method") != "web_search"
            ):
                web_result = await _verify_claim_externally(
                    claim, model, force_web_search=True,
                    expert_mode=expert_mode, response_context=response_context, claim_context=claim_context,
                )
                if web_result.get("status") in ("verified", "unverified"):
                    return await _cache_result({
                        "claim": claim,
                        "status": web_result["status"],
                        "similarity": web_result["confidence"],
                        "reason": web_result["reason"],
                        "verification_method": web_result.get(
                            "verification_method", "web_search",
                        ),
                        "verification_model": web_result.get("verification_model"),
                        "source_urls": web_result.get("source_urls", []),
                        **({"credit_exhausted": True} if web_result.get("credit_exhausted") else {}),
                    })
            # All methods exhausted — return uncertain with all available context
            return {
                "claim": claim,
                "status": "uncertain",
                "similarity": round(similarity, 3),
                "reason": details.get("reason", "Partial match — review recommended"),
                "verification_details": details,
                "verification_method": "kb",
                "source_urls": ext_result.get("source_urls", []),
                **_kb_source_fields(top_result),
            }

    except Exception as e:
        logger.warning("Claim verification failed for '%s...': %s", claim[:50], e)
        return {
            "claim": claim,
            "status": "error",
            "similarity": 0.0,
            "reason": str(e),
        }


# ---------------------------------------------------------------------------
# Consistency checking (cross-turn + internal)
# ---------------------------------------------------------------------------

async def _check_history_consistency(
    claims: list[str],
    conversation_history: list[dict[str, str]] | None,
) -> list[dict[str, Any]]:
    """Check claims for contradictions against conversation history and each other.

    Makes a single LLM call to detect:
    1. Claims that contradict prior assistant statements (cross-turn)
    2. Claims that logically contradict each other (internal)

    Returns a list of issues, each with claim_index, contradiction description,
    and type ("history" or "internal").
    """
    if not claims:
        return []

    # Build prior context from conversation history
    prior_context = ""
    if conversation_history:
        prior_msgs = [
            m for m in conversation_history
            if m.get("role") == "assistant" and m.get("content", "").strip()
        ]
        if prior_msgs:
            prior_context = "\n\n".join(
                f"[Prior turn {i + 1}]: {m['content'][:2000]}"
                for i, m in enumerate(prior_msgs[-3:])
            )

    # If no history and fewer than 2 claims, nothing to check
    if not prior_context and len(claims) < 2:
        return []

    # Build the claims list for the prompt
    claims_text = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))

    user_prompt_parts = ["Current claims from the latest response:"]
    user_prompt_parts.append(claims_text)

    if prior_context:
        user_prompt_parts.insert(0, "Prior conversation context:")
        user_prompt_parts.insert(1, prior_context)
        user_prompt_parts.insert(2, "---")

    user_prompt = "\n\n".join(user_prompt_parts)

    try:
        # Consistency checking requires nuanced cross-text comparison —
        # use the dedicated consistency model (Gemini 2.5 Flash by default)
        # instead of GPT-4o-mini for more reliable contradiction detection.
        messages = [
            {"role": "system", "content": _SYSTEM_CONSISTENCY_CHECK},
            {"role": "user", "content": user_prompt},
        ]

        from core.utils.llm_client import call_llm_raw
        data = await call_llm_raw(
            messages,
            model=config.VERIFICATION_CONSISTENCY_MODEL,
            temperature=0.0,
            max_tokens=400,
            timeout=config.BIFROST_TIMEOUT,
            breaker_name="bifrost-verify",
        )
        raw_answer = data["choices"][0]["message"].get("content", "").strip()

        # Parse JSON array from response
        parsed = parse_llm_json(raw_answer)
        if isinstance(parsed, list):
            issues = []
            for item in parsed:
                if isinstance(item, dict) and "claim_index" in item:
                    issues.append({
                        "claim_index": int(item["claim_index"]),
                        "contradiction": item.get("contradiction", ""),
                        "conflicting_claim_index": item.get("conflicting_claim_index"),
                        "type": item.get("type", "history"),
                    })
            return issues
        return []

    except (CircuitOpenError, Exception) as e:
        logger.warning("Consistency check failed: %s", e)
        return []
