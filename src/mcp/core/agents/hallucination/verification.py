# Copyright (c) 2026 Cerid AI. All rights reserved.
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
import time
from datetime import datetime
from typing import Any

import config
from core.agents.hallucination.enums import VerificationStatus
from core.agents.hallucination.escalation import (
    EscalationTier,
    GroundingSignals,
    get_escalation_policy,
)
from core.agents.hallucination.extraction import _reclassify_recency
from core.agents.hallucination.freshness import (
    ClaimFreshness,
    classify_claim_freshness,
    weight_evidence_by_recency,
)
from core.agents.hallucination.grounding_verifier import (
    NLI_PREMISE_CHAR_LIMIT,
    get_grounding_verifier,
)
from core.agents.hallucination.patterns import (
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
    memory_authority_boost,
)
from core.context.identity import with_tenant_scope
from core.utils.circuit_breaker import CircuitOpenError, NonTransientError
from core.utils.claim_cache import (
    TIME_SENSITIVE_VERDICT_TTL_S,
    cache_verdict,
    get_cached_verdict,
)
from core.utils.embeddings import l2_distance_to_relevance
from core.utils.llm_parsing import parse_llm_json
from core.utils.swallowed import log_swallowed_error


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

# Deadline geometry for per-claim verification. The streaming caller wraps
# each claim in wait_for(claim_timeout); every LLM/fetch budget inside the
# claim must fit what remains of that window or the outer timeout is
# guaranteed to fire mid-call (the pre-2026-07-13 failure mode: inner web
# calls were granted BIFROST_TIMEOUT*2=40s under a 25s outer cap).
_DEADLINE_SAFETY_MARGIN_S = 1.0
# Below this remaining budget an external call cannot plausibly finish —
# return a retryable timeout verdict instead of starting a doomed call.
_MIN_EXTERNAL_CALL_BUDGET_S = 3.0

# ``memory_source_type`` stamped by verified_memory.promote_verified_facts on
# memories it writes to the conversations collection. Such memories are
# INADMISSIBLE as verification evidence: a prior verdict must not become the
# evidence that confirms the next similar claim (self-reinforcing loop).
_VERIFICATION_MEMORY_SOURCE = "verification"

# Minimum cross-model verdict confidence for a "supported" verdict to promote a
# claim to "verified" (vs. graded "uncertain"). Phase 3.5 calibration HELD this
# at 0.5: the labeled harness's mid confidence band (0.35-0.64) is degenerate
# (n=5) and an exhaustive (lo, hi) threshold sweep found no move that improves
# accuracy — so the bare literal is *named*, not changed. Module constant per
# file convention (no env knob: calibration says there is nothing to tune yet).
_SUPPORTED_MIN_CONFIDENCE = 0.5


def _remaining_budget(deadline: float | None) -> float | None:
    """Seconds left before *deadline* (monotonic), or None when unbounded."""
    if deadline is None:
        return None
    return deadline - time.monotonic()


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
    "Judge ONLY the quoted claim. Surrounding text and other claims from the "
    "same response are context for resolving references — do NOT refute the "
    "claim because something ELSE in the response is wrong.\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "1-2 sentence explanation"}\n\n'
    "Rules:\n"
    "- \"supported\": The claim is factually accurate\n"
    "- \"refuted\": The claim contains clear factual errors you can point to\n"
    "- \"insufficient_info\": You cannot confidently verify or refute. This "
    "INCLUDES claims about live/dynamic data you cannot snapshot "
    "(stock prices, current weather, live sports scores, today's temperatures, "
    "real-time ridership, etc.) — these are UNVERIFIABLE, not REFUTED. "
    "Use \"insufficient_info\" for them.\n"
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
    "A different AI model was asked a question but evaded answering — instead "
    "giving hedging language, deflections, or generic disclaimers about "
    "complexity.\n\n"
    "Your job is to judge whether that evasion was JUSTIFIED. First classify "
    "the question into exactly one of three kinds:\n"
    "1. It has a SINGLE decisive answer that settles it — one specific number, "
    "date, name, measurement, or universally-agreed fact (e.g. the boiling "
    "point of water). Find it and report it; the evasion was unjustified.\n"
    "2. Its honest answer is a COMBINATION of many contributing factors, a "
    "matter of ongoing debate, a subjective or value judgment, or 'it depends' "
    "— there is no single decisive answer, even if you can name some "
    "contributing factors or examples. The model's caution is defensible.\n"
    "3. The specific information genuinely does not exist or is impossible to "
    "determine (e.g. an unknowable future outcome).\n"
    "Naming a few contributing factors does NOT make a question kind 1: if no "
    "single fact settles it, it is kind 2. Do NOT force a concrete answer onto "
    "a question of kind 2 or 3."
    f"{_EMPIRICAL_SOURCE_GUIDANCE}\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "Your classification and, for kind 1, the concrete answer '
    'with source citations"}\n\n'
    "Rules:\n"
    "- \"supported\": kind 1 — a single decisive, checkable answer exists that "
    "the model evaded. Give the actual answer with sources.\n"
    "- \"insufficient_info\": kind 2 — the answer is a combination of factors, "
    "an ongoing debate, or a subjective judgment with no single decisive "
    "answer, so the model's hedge is defensible.\n"
    "- \"refuted\": kind 3 — the specific data genuinely does not exist or is "
    "legitimately impossible to determine.\n"
    "- Include specific numbers, percentages, or data points in your "
    "reasoning when the answer is concrete (kind 1).\n"
    "- confidence: how certain you are of the CLASSIFICATION "
    "(0.0 = unsure, 1.0 = certain)"
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
# Cited-URL verification (Task 12 / audit V-3)
# ---------------------------------------------------------------------------

async def _verify_against_cited_url(
    claim_text: str,
    url: str,
    *,
    timeout: float = 8.0,
    max_bytes: int = 300_000,
) -> dict[str, Any]:
    """Fetch the LLM's cited URL and NLI-entail the claim against its body.

    This plugs the audit V-3 hole: the verifier used to ignore cited URLs and
    re-search from claim text, letting fabricated citations
    ("According to https://wikipedia.org/foo, the sky is green") pass if an
    unrelated web-search result happened to confirm the claim. Now the cited
    page itself is the premise.

    Returns a verdict dict shaped like other verification methods:
    ``{status, similarity, verification_method, verification_model,
    source_urls, reasoning}``. The caller should fall through to the normal
    KB + web-search path when this function raises (fetch error, NLI failure)
    or returns ``status == "uncertain"``.
    """
    from html.parser import HTMLParser

    from core.ingest.sources.safe_fetch import guarded_get

    # SSRF guard: the cited URL comes from LLM output (attacker-influenceable
    # via prompt injection / poisoned KB), so route it through the shared
    # per-hop-revalidating fetch instead of a raw redirect-following client.
    # A blocked/internal target raises ValueError → the caller's except falls
    # through to the KB + web-search path, same as any other fetch error.
    resp = await guarded_get(url, user_agent="cerid-verifier/1", timeout=timeout)
    resp.raise_for_status()
    body = resp.text[:max_bytes]

    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self._skip = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in ("script", "style", "noscript"):
                self._skip = True

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style", "noscript"):
                self._skip = False

        def handle_data(self, data: str) -> None:
            if self._skip:
                return
            stripped = data.strip()
            if stripped:
                self.parts.append(stripped)

    ext = _TextExtractor()
    try:
        ext.feed(body)
        text = " ".join(ext.parts)
    except Exception as exc:
        log_swallowed_error(f"{__name__}.cited_url_html_parse", exc)
        text = body  # fall back to raw body if HTML parsing explodes

    # NLI context window — cross-encoder/nli-deberta-v3-xsmall truncates to
    # 512 tokens (~2k chars). 4000 chars is an upper bound; the tokenizer
    # truncates from there.
    premise = text[:4000]
    # Route through the pluggable grounding verifier (Phase 3.1) — the second
    # NLI site. Async/batched: the sync variant runs ONNX inference on the event
    # loop and stalls every concurrent claim verification in the stream.
    nli_result = await get_grounding_verifier().score(premise, claim_text)

    entail = float(nli_result.get("entailment", 0.0))
    contra = float(nli_result.get("contradiction", 0.0))

    # Use the SAME configured NLI thresholds as every other verification band
    # (KB-NLI main path, self_rag). Previously hardcoded 0.5/0.6, which made the
    # cited-URL path simultaneously looser on entailment (>0.6 vs the configured
    # 0.7) and stricter on contradiction (>0.5 vs 0.6) than the rest of the
    # system — so a claim entailed at 0.6-0.69 by its own cited source showed
    # "verified" while the identical score against a KB source showed "uncertain".
    if contra >= config.NLI_CONTRADICTION_THRESHOLD:
        status = "unverified"
    elif entail >= config.NLI_ENTAILMENT_THRESHOLD:
        status = "verified"
    else:
        status = "uncertain"

    return {
        "status": status,
        "similarity": round(entail, 3),
        "verification_method": "cited_url",
        "verification_model": "nli-onnx",
        "source_urls": [url],
        "reasoning": (
            f"NLI against cited URL body (entail={entail:.2f}, "
            f"contra={contra:.2f})"
        ),
    }


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
    """Map an external verifier's answerability judgment onto an evasion verdict.

    The evasion verifier classifies the *question the model evaded* (see
    ``_SYSTEM_EVASION_VERIFICATION``) into one of three kinds, which arrive here
    already funneled through ``_parse_verification_verdict``:

    * "verified" (verifier said *supported* — a concrete, checkable answer
      exists) → the evasion was unjustified → **unverified**. Confidence is
      clamped into the unverified band (<= 0.35, the same clamp the direct
      ``refuted`` path uses) so a hedge never surfaces as unverified@~1.0.
    * "uncertain" (verifier said *insufficient_info* — the question is genuinely
      contested / subjective / multi-causal with no single objective answer) →
      the model's hedge is defensible → **uncertain**, at a clamped mid-band
      confidence. This is the outcome the type-aware path previously lacked: a
      hedge about a genuinely open question is graded uncertain, not inverted to
      a high-confidence unverified.
    * "unverified" (verifier said *refuted* — the specific data genuinely does
      not exist / is unknowable) → the model's caution was justified →
      **verified**.
    """
    status = verdict["status"]
    reasoning = verdict.get("reason", "")

    if status == "verified":
        # A concrete answer exists — model's evasion was unjustified.
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
            # Align with the direct refuted→unverified clamp (parser: <= 0.35);
            # the inbound score is the verifier's confidence the answer EXISTS,
            # not a confidence the claim is supported, so it must not ride
            # through onto the unverified verdict.
            "confidence": round(min(verdict.get("confidence", 0.35), 0.35), 3),
            "reason": (
                f"Model evaded answering — data is available: {clean_reason}"
            ).rstrip(": "),
        }

    if status == "uncertain":
        # Genuinely contested / no single objective answer — hedge is defensible.
        clean_reason = reasoning
        for prefix in (
            "Claim not independently verifiable: ",
            "Claim not independently verifiable",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "uncertain",
            # Mid band, matching the parser's uncertain clamp (0.36–0.64).
            "confidence": round(max(0.36, min(0.64, verdict.get("confidence", 0.5))), 3),
            "reason": (
                "Model's hedge is defensible — the question has no single "
                f"objective answer: {clean_reason}"
            ).rstrip(": "),
        }

    if status == "unverified":
        # Data genuinely unavailable / unknowable — evasion was justified.
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

    # error / unrecognized — keep as-is
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

    if match_ratio >= 0.95:
        return 0.03   # Exact match
    elif match_ratio >= 0.90:
        return 0.01   # Near match
    elif match_ratio >= 0.75:
        return 0.00   # Neutral — close enough to not penalise
    else:
        return -0.03  # Significant disagreement (proportional)


def _verify_fact_relationship(
    claim: str,
    top_result: dict[str, Any],
) -> dict[str, Any]:
    """Verify the KB match is about the SAME facts, not just the same topic.

    Goes beyond "I found a match" to check:
    1. Temporal alignment — claim says "2024" but source says "1990" → flag
    2. Entity alignment — claim and source discuss different aspects of same topic
    3. Specificity — claim makes numeric assertion but source is topic-general

    Returns: {"aligned": bool, "reason": str, "confidence_adjustment": float}
    """
    source_text = top_result.get("content", "")[:500]
    if not source_text:
        return {"aligned": True, "reason": "no_source_text", "confidence_adjustment": 0.0}

    adjustment = 0.0
    reasons: list[str] = []

    # Check 1: Temporal alignment — decade-level mismatch between claim and source
    claim_years = set(YEAR_RE.findall(claim))
    source_years = set(YEAR_RE.findall(source_text))
    if claim_years and source_years and not claim_years & source_years:
        # Years mentioned but none overlap — check if decades differ
        claim_decades = {int(y) // 10 for y in claim_years}
        source_decades = {int(y) // 10 for y in source_years}
        if not claim_decades & source_decades:
            adjustment -= 0.04
            reasons.append(f"temporal_mismatch: claim={claim_years} vs source={source_years}")

    # Check 2: Specificity — claim has numbers but source only mentions topic generally
    claim_numbers = set(re.findall(r"\b\d[\d,.]+\b", claim))
    source_numbers = set(re.findall(r"\b\d[\d,.]+\b", source_text))
    if len(claim_numbers) >= 2 and not source_numbers:
        adjustment -= 0.03
        reasons.append("specificity_gap: claim has numbers, source is general")

    # Check 3: Percentage contradiction (beyond simple presence check)
    claim_pcts = PERCENT_RE.findall(claim)
    source_pcts = PERCENT_RE.findall(source_text)
    if claim_pcts and source_pcts:
        # If both have percentages but they differ by >20pp, flag
        try:
            claim_vals = [float(p.rstrip("%")) for p in claim_pcts]
            source_vals = [float(p.rstrip("%")) for p in source_pcts]
            for cv in claim_vals:
                if all(abs(cv - sv) > 20 for sv in source_vals):
                    adjustment -= 0.03
                    reasons.append(f"percentage_gap: claim={cv}% vs source={source_vals}")
                    break
        except (ValueError, TypeError):
            pass

    aligned = adjustment >= -0.02  # Small adjustments are tolerable
    reason = "; ".join(reasons) if reasons else "aligned"
    return {"aligned": aligned, "reason": reason, "confidence_adjustment": adjustment}


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

    # Factor 5: Fact-relationship verification (temporal + entity alignment)
    fact_rel = _verify_fact_relationship(claim, top_results[0])
    adjustment += fact_rel["confidence_adjustment"]

    # NOTE: NLI contradiction scoring is handled by the main NLI check in
    # verify_claim() (not here) to avoid double-counting. This function
    # stays zero-LLM-cost per its contract.

    # Cap total adjustment
    adjustment = max(-0.08, min(0.08, adjustment))

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
    LLM responses (which would cause circular self-verification), and further
    excludes verification-promoted memories (``memory_source_type ==
    "verification"``) so a prior verdict can never be re-served as the evidence
    that confirms the next similar claim. ``$ne`` still matches rows that omit
    the key (genuine user memories carry no ``memory_source_type``), so honest
    empirical/decision/preference facts remain admissible.
    """
    try:
        collection = chroma_client.get_collection(
            name=config.collection_name("conversations")
        )
        results = collection.query(
            query_texts=[claim],
            n_results=top_k,
            where=with_tenant_scope({
                "$and": [
                    {"memory_type": {"$in": MEMORY_TYPES}},
                    {"memory_source_type": {"$ne": _VERIFICATION_MEMORY_SOURCE}},
                ]
            }),
            include=["documents", "metadatas", "distances"],
        )

        formatted = []
        if results["ids"] and results["ids"][0]:
            for i, _chunk_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 1.0
                relevance = l2_distance_to_relevance(distance)
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                formatted.append({
                    "relevance": round(relevance, 4),
                    "artifact_id": metadata.get("artifact_id", ""),
                    "filename": metadata.get("filename", ""),
                    "domain": "conversations",
                    "content": results["documents"][0][i] if results["documents"] else "",
                    "memory_type": metadata.get("memory_type", ""),
                    "memory_source": True,
                    "created_at": metadata.get("created_at") or metadata.get("ingested_at") or None,
                })
        return formatted
    except Exception as e:
        log_swallowed_error('core.agents.hallucination.verification', e)
        logger.debug("Memory query failed (non-blocking): %s", e)
        return []


# ---------------------------------------------------------------------------
# Temporal evidence staleness (Phase 4.2)
# ---------------------------------------------------------------------------

def _evidence_is_stale(top_result: dict[str, Any], window_days: int) -> bool:
    """True when the KB evidence's date is older than the staleness window.

    Reads ``created_at`` (preferred) or ``ingested_at`` from the matched KB
    chunk (Slice 2 Phase 1.1 threads these through ``_format_chroma_result``).

    Conservative on absence: when no parseable date is present, returns
    ``False`` — we never manufacture doubt from a missing timestamp, only
    from a *known-old* one. The staleness downgrade applies on top of the
    existing temporal-claim escalation, so the worst case of a missing date
    is the prior behavior (verified), not a regression.
    """
    date_str = top_result.get("created_at") or top_result.get("ingested_at")
    if not date_str:
        return False
    from core.utils.time import utcnow

    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
        age_days = (utcnow().replace(tzinfo=None) - dt_naive).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return False
    return age_days > window_days


def _stale_evidence_verdict(
    claim: str, top_result: dict[str, Any], similarity: float,
) -> dict[str, Any]:
    """Build the ``uncertain`` / ``stale_evidence`` verdict for a temporal
    claim supported only by KB evidence older than the verification window.

    The UI already renders ``uncertain`` (amber) — no frontend change. The
    ``reason`` carries ``stale_evidence`` so dashboards / the audit pane can
    distinguish "we couldn't confirm currency" from "we couldn't find it".
    """
    _date = top_result.get("created_at") or top_result.get("ingested_at") or "unknown"
    return {
        "claim": claim,
        "status": "uncertain",
        "similarity": round(min(similarity, 0.64), 3),
        "reason": (
            "stale_evidence: claim is time-sensitive but the only supporting "
            f"KB evidence is dated {str(_date)[:10]} (older than the "
            "verification staleness window) and live verification was "
            "inconclusive — currency cannot be confirmed"
        ),
        "stale_evidence": True,
        "evidence_date": str(_date)[:10] if _date != "unknown" else None,
        "source_artifact_id": top_result.get("artifact_id", ""),
        "source_filename": top_result.get("filename", ""),
        "source_domain": top_result.get("domain", ""),
        "source_snippet": top_result.get("content", "")[:200],
        "memory_source": bool(top_result.get("memory_source")),
        "verification_method": "kb_stale_evidence",
    }


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

        if verdict == "supported" and confidence >= _SUPPORTED_MIN_CONFIDENCE:
            status = "verified"
        elif verdict == "refuted":
            status = "unverified"
            # Refuted claims get low confidence even if model says high
            confidence = min(confidence, 0.35)
        else:
            # insufficient_info, unrecognized, or low-confidence supported
            # → uncertain (neutral). Use the LLM's reported confidence as a
            # signal — a claim with 0.4 confidence from the verifier is closer
            # to resolution than one with 0.1.
            status = "uncertain"
            # Clamp uncertain confidence to 0.36-0.64 range so it never
            # crosses into verified (>= threshold) or unverified (<= 0.35)
            confidence = max(0.36, min(0.64, confidence))

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
# Independent search evidence (Phase 3.3, 2026-07-13 quality program)
# ---------------------------------------------------------------------------
# External evidence URLs previously came almost exclusively from OpenRouter
# ``:online``-annotation citations (single-vendor sourcing). This adds URLs
# from the SearXNG/Tavily chain in ``utils/web_search.py`` as independent
# evidence for the same claim, so a claim's evidence set isn't sourced from
# one vendor's search index.
_WEB_SEARCH_EVIDENCE_TOP_N = 3


async def _independent_search_evidence_urls(
    claim: str, deadline: float | None
) -> list[str]:
    """Fetch up to :data:`_WEB_SEARCH_EVIDENCE_TOP_N` URLs from the configured
    ``utils.web_search`` provider chain (SearXNG/Tavily) for *claim*.

    Skips gracefully (returns ``[]``, never raises into the caller) when:
    - no real provider (Tavily/SearXNG) is configured — the always-on
      OpenRouter ``:online`` fallback is deliberately excluded here since it
      would add a second synthesized-answer LLM call on top of the verdict
      call already in flight for this claim;
    - too little of the caller's per-claim deadline budget remains to
      plausibly complete a search, mirroring the external-call budget gate
      above.
    """
    from utils.web_search import has_real_search_provider

    if not has_real_search_provider():
        return []

    _budget = _remaining_budget(deadline)
    if _budget is not None and _budget < _MIN_EXTERNAL_CALL_BUDGET_S:
        return []

    try:
        from utils.web_search import search_and_verify

        search_result = await search_and_verify(
            claim, max_results=_WEB_SEARCH_EVIDENCE_TOP_N,
        )
        return [
            r["url"] for r in search_result.get("results", []) if r.get("url")
        ]
    except Exception as exc:  # noqa: BLE001 — evidence gathering is best-effort
        log_swallowed_error(f"{__name__}.independent_search_evidence", exc)
        return []


def _extract_citation_urls(message: dict[str, Any]) -> list[str]:
    """De-duplicate OpenRouter ``url_citation`` annotation URLs from an LLM
    message, preserving first-seen order.

    ``:online``-suffixed models attach web sources as ``url_citation``
    annotations. Both the single-claim (:func:`_verify_claim_externally`) and
    batch (:func:`verify_claims_batch_external`) external verifiers extract them
    identically; this is the shared core so the annotation shape is parsed in
    exactly one place.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for annotation in message.get("annotations", []) or []:
        if annotation.get("type") == "url_citation":
            url_str = annotation.get("url_citation", {}).get("url", "")
            if url_str and url_str not in seen:
                seen.add(url_str)
                urls.append(url_str)
    return urls


# ---------------------------------------------------------------------------
# External (Cross-Model) Verification — Direct Structured Verdict
# ---------------------------------------------------------------------------

async def _verify_claim_externally(
    claim: str,
    generating_model: str | None = None,
    force_web_search: bool = False,
    streaming: bool = False,
    expert_mode: bool = False,
    claim_context: str | None = None,
    response_context: str | None = None,
    kb_snippet: str | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Direct structured cross-model verification for a single claim.

    ``deadline`` is a ``time.monotonic()`` timestamp by which the caller's
    per-claim budget expires. The LLM call timeout is clamped to fit it, and
    when too little budget remains to plausibly complete a call the function
    returns a retryable ``timeout`` verdict instead of starting one — this is
    what keeps sequential fallback chains (cross-model → forced web →
    authoritative) from overrunning the caller's ``wait_for``.

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

    # External verification always egresses to OpenRouter (call_llm_raw is
    # hard-wired to it), carrying claim text and KB snippets. On a local-provider
    # install that contradicts the operator's choice, so honour the opt-out.
    if not getattr(config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", True):
        from core.routing.provider_state import is_local_provider
        if is_local_provider():  # no-arg = the *active* provider
            return {
                "status": "uncertain",
                "confidence": 0.3,
                "reason": (
                    "External verification skipped — local inference provider "
                    "active and ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false"
                ),
                "verification_method": "none",
                "source_urls": [],
            }

    # Deadline gate: with almost no budget left, an external call can only
    # end in the caller's wait_for firing mid-flight. Return a retryable
    # timeout verdict (never cached — see _is_transport_failure_verdict;
    # picked up by the streaming retry sweep) instead of starting one.
    _budget = _remaining_budget(deadline)
    if _budget is not None and _budget < _MIN_EXTERNAL_CALL_BUDGET_S:
        return {
            "status": "uncertain",
            "confidence": 0.3,
            "reason": "Per-claim budget exhausted before external verification",
            "verification_method": "timeout",
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
    #
    # An explicit stale-knowledge admission (_is_recency_claim) still wins the
    # recency route even when it also reads as ignorance. But the keyword-based
    # _reclassify_recency reclassification must NOT hijack a first-person
    # inability admission that merely mentions a temporal word: "I cannot access
    # real-time data for *current* stock prices" is an ignorance admission about
    # live data, not a stale factual claim — "current" would otherwise route it
    # to the recency path, which has no data point to compare and grades it
    # uncertain (verdict-harness residue V-42 / IG-04).
    is_ignorance_admission = _is_ignorance_admission(claim)
    is_recency = (
        not is_evasion and not is_citation
        and (
            _is_recency_claim(claim)
            or (
                not is_ignorance_admission
                and _reclassify_recency(claim, "factual") == "recency"
            )
        )
    )

    # Detect ignorance-admitting claims ("I don't have info about X")
    # These always use web search and get inverted verdicts.
    # Exclude recency claims — they have separate handling.
    is_ignorance = (
        not is_evasion and not is_citation and not is_recency
        and is_ignorance_admission
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
    # AND gather authoritative external evidence before sending to LLM
    _authoritative_evidence: str = ""
    # Structured copy of the authoritative_verify result so downstream consumers
    # (SSE stream, audit log, verified-memory promotion) can show *which* sources
    # drove the verdict — previously the LLM-facing prompt string was the only
    # artifact that survived, hiding per-source NLI scores and domain classification.
    _authoritative_result: dict[str, Any] | None = None
    if expert_mode:
        if is_current_event:
            verify_model = config.VERIFICATION_EXPERT_WEB_MODEL
        else:
            verify_model = config.VERIFICATION_EXPERT_MODEL
        logger.debug("Expert mode: using %s for claim verification", verify_model)

        # Gather authoritative external evidence — LLM synthesizes, data is source of truth
        if getattr(config, "EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", True):
            try:
                from core.agents.hallucination.authoritative_verify import (
                    verify_claim_authoritatively,
                )

                auth_result = await verify_claim_authoritatively(
                    claim,
                    kb_results=[{"content": kb_snippet}] if kb_snippet else None,
                    conversation_context=conversation_context,
                )
                _authoritative_result = auth_result
                auth_sources = auth_result.get("authoritative_sources", [])
                if auth_sources:
                    evidence_lines = [
                        f"- [{s['source']}] (NLI entailment: {s['nli_entailment']:.2f}): {s['content']}"
                        for s in auth_sources[:3]
                    ]
                    _authoritative_evidence = (
                        "\n\nAuthoritative external evidence:\n"
                        + "\n".join(evidence_lines)
                        + f"\n\nEvidence summary: {auth_result.get('evidence_summary', '')}"
                    )
            except Exception as exc:  # noqa: BLE001 — evidence gathering is best-effort
                log_swallowed_error(__name__, exc)

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

            # Build surrounding-text block once — used across all prompt paths
            # to give the verifier full context (e.g. a table row that only
            # makes sense with the preceding header).
            ctx_block = (
                f"\n\nSurrounding text from the response:\n\"{claim_context}\"\n"
                if claim_context else ""
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
                    f"{ctx_block}{context_line}{model_context}\n\n{_json_response_fmt}"
                )
            elif is_ignorance:
                # Reframed prompt: check underlying facts, not the model's honesty
                user_prompt = (
                    f"An AI model said: \"{claim}\"\n\n"
                    f"The model is admitting it lacks knowledge about a topic. "
                    f"Do NOT evaluate whether the model is honest about its "
                    f"limitations. Instead, search for and verify whether the "
                    f"underlying facts, events, or information actually exist."
                    f"{ctx_block}{context_line}{model_context}\n\n{_json_response_fmt}"
                )
            else:
                # Include KB snippet when available — gives the verifier
                # partial evidence from the user's knowledge base to
                # triangulate against, reducing false "uncertain" verdicts.
                _ext_nli_label = ""
                _ext_nli_conf = ""
                if kb_snippet:
                    try:
                        # v0.93.10: async-batched NLI for coalescing with
                        # the concurrent verify_claim() calls in the same
                        # asyncio.gather(). See line ~1804 for the full
                        # rationale. Phase 3.1: routed through the grounding
                        # verifier + widened to NLI_PREMISE_CHAR_LIMIT (the old
                        # [:512]-char slice starved expert-mode's 1200-char
                        # snippet of ~half its evidence before the tokenizer's
                        # 512-*token* budget even applied).
                        _ext_nli = await get_grounding_verifier().score(
                            kb_snippet[:NLI_PREMISE_CHAR_LIMIT], claim
                        )
                        _ext_nli_label = _ext_nli["label"]
                        _ext_nli_conf = (
                            f"entailment={_ext_nli['entailment']:.2f}, "
                            f"contradiction={_ext_nli['contradiction']:.2f}"
                        )
                    except Exception as exc:
                        log_swallowed_error('core.agents.hallucination.verification', exc)
                        _ext_nli_label = "unknown"
                        _ext_nli_conf = ""
                kb_block = (
                    f"\n\nEvidence from knowledge base ({_ext_nli_label}"
                    f"{', ' + _ext_nli_conf if _ext_nli_conf else ''}):\n"
                    f"\"{kb_snippet}\"\n"
                    if kb_snippet else ""
                )
                user_prompt = (
                    f"Assess this claim for factual accuracy:\n\n"
                    f"\"{claim}\"{ctx_block}{kb_block}{context_line}{model_context}\n\n{_json_response_fmt}"
                )

            # Inject authoritative evidence and conversation context in expert mode
            if expert_mode:
                if _authoritative_evidence:
                    user_prompt += _authoritative_evidence
                if conversation_context:
                    recent = conversation_context[-10:]  # Last 5 exchanges
                    ctx_lines = "\n".join(
                        f"[{m.get('role', '?')}]: {m.get('content', '')[:200]}"
                        for m in recent
                    )
                    user_prompt += f"\n\nConversation context:\n{ctx_lines}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Increase timeout for web-search calls — they take longer.
            # Clamp to the caller's remaining per-claim budget: an inner
            # timeout larger than the outer wait_for guarantees the claim
            # dies as "timeout" while the call is still in flight.
            timeout = config.BIFROST_TIMEOUT * 2 if is_current_event else config.BIFROST_TIMEOUT
            _budget = _remaining_budget(deadline)
            if _budget is not None:
                timeout = max(
                    min(timeout, _budget - _DEADLINE_SAFETY_MARGIN_S),
                    _MIN_EXTERNAL_CALL_BUDGET_S,
                )

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
            source_urls: list[str] = _extract_citation_urls(raw_message)

            # Independent evidence (Phase 3.3): merge SearXNG/Tavily results
            # for this claim so evidence isn't sourced from OpenRouter alone.
            # Only for the web-search verification path — cross-model claims
            # don't carry a web-search premise for these URLs to support.
            if is_current_event:
                seen_urls: set[str] = set(source_urls)
                for url_str in await _independent_search_evidence_urls(claim, deadline):
                    if url_str not in seen_urls:
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
                    claim_context=claim_context,
                    deadline=deadline,
                )

            return {
                **verdict,
                "verification_method": verification_method,
                "verification_model": verify_model,
                "verification_answer": raw_answer,
                "source_urls": source_urls,
                **(
                    {
                        "authoritative_sources": _authoritative_result.get("authoritative_sources", []),
                        "claim_domain": _authoritative_result.get("claim_domain", "general"),
                        "cross_validation": _authoritative_result.get("cross_validation", {}),
                        "evidence_summary": _authoritative_result.get("evidence_summary", ""),
                    }
                    if _authoritative_result
                    else {}
                ),
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
            log_swallowed_error(f"{__name__}.external_verify", e)
            logger.warning("External verification failed for '%s...': %s", claim[:50], e)
            return {
                "status": "uncertain",
                "confidence": 0.3,
                "reason": f"External verification failed: {e}",
                "verification_method": f"{verification_method}_failed",
                "source_urls": [],
            }


def _is_transport_failure_verdict(result: dict[str, Any]) -> bool:
    """True when the verdict is an outage artifact, not a judgment.

    Timeout / 402 credit exhaustion / open breaker / provider-error paths
    all return status "uncertain" with a failure verification_method —
    caching those keeps serving the outage after it passes (2026-07-10:
    a credit exhaustion poisoned cached verdicts across eval runs).
    """
    method = result.get("verification_method", "") or ""
    return (
        method in ("timeout", "credit_exhausted", "circuit_open", "none")
        or method.endswith("_failed")
    )


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
# External verdict envelope assembly
# ---------------------------------------------------------------------------
# ``_verify_claim_externally`` reports its verdict score under ``confidence``
# (the LLM's verdict confidence). Every downstream consumer of a ``verify_claim``
# verdict — the ``claim_verified`` SSE event (which reads ``result["similarity"]``
# for its ``confidence`` field), the Redis report aggregate, and the fact cache
# (``cache_verdict`` stores ``verdict["similarity"]``) — expects the score under
# ``similarity``. These two helpers are the single place that maps
# ``confidence → similarity`` so no external-return path can leak a verdict
# missing ``similarity`` (the pre-2026-07-14 defect where terminal verified/
# unverified verdicts on the escalation paths arrived over the SSE stream with
# confidence=0.0 — calibration cases V-94, TS-03).


def _external_verdict(
    claim: str,
    ext_result: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    """Whitelisted verdict envelope from an external verification result.

    Used by the KB-fallback escalation paths (no KB evidence, low similarity,
    KB-unverified, KB-uncertain), which surface only the scalar verdict + its
    sources. ``extra`` folds in per-path annotations (e.g. ``circular_source``).
    """
    verdict: dict[str, Any] = {
        "claim": claim,
        "status": ext_result["status"],
        "similarity": ext_result["confidence"],
        "reason": ext_result["reason"],
        "verification_method": ext_result.get("verification_method", "none"),
        "verification_model": ext_result.get("verification_model"),
        "source_urls": ext_result.get("source_urls", []),
    }
    if ext_result.get("credit_exhausted"):
        verdict["credit_exhausted"] = True
    verdict.update(extra)
    return verdict


def _escalated_external_verdict(
    claim: str,
    ext_result: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    """Verdict envelope that preserves the FULL external payload.

    Unlike :func:`_external_verdict`, the NLI-entailment / NLI-contradiction /
    semantic-alignment escalation paths keep every field the external verifier
    produced (``verification_answer`` plus, in expert mode, the authoritative
    evidence bundle: ``authoritative_sources`` / ``claim_domain`` /
    ``cross_validation`` / ``evidence_summary``) so the audit UI can show *why*
    the escalation resolved the way it did. It still normalizes the score into
    the ``similarity`` envelope field. ``extra`` folds in the per-path
    escalation markers (``kb_nli_escalated`` etc.).
    """
    return {
        **ext_result,
        "claim": claim,
        "similarity": ext_result["confidence"],
        **extra,
    }


# ---------------------------------------------------------------------------
# Batch external verification (same-model claim grouping)
# ---------------------------------------------------------------------------

_SYSTEM_BATCH_VERIFICATION = (
    "You are a fact-checking engine. Verify each claim for factual accuracy. "
    "Judge each claim ONLY on its own text — other claims in the batch and "
    "any provided context are for resolving references, and an error in one "
    "claim must not change another claim's verdict. "
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

        # Extract source URLs from annotations (web search models) — shared
        # dedup core with the single-claim external verifier.
        source_urls: list[str] = _extract_citation_urls(data["choices"][0]["message"])

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
            confidence = verdict_obj.get("confidence", 0.5)
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
        log_swallowed_error('core.agents.hallucination.verification', exc)
        logger.warning("Batch verification failed (%s), claims will fall back to individual", exc)

    return results


# ---------------------------------------------------------------------------
# Main claim verification
# ---------------------------------------------------------------------------

async def verify_claims(
    claims: list[str],
    chroma_client,
    neo4j_driver=None,
    redis_client=None,
    *,
    threshold: float | None = None,
    model: str | None = None,
    streaming: bool = False,
    expert_mode: bool = False,
    response_context: str | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    source_artifact_ids: list[str] | None = None,
    claim_context: str | None = None,
    deadline: float | None = None,
    stale_context: bool = False,
) -> list[dict[str, Any]]:
    """Verify a batch of already-extracted claims concurrently.

    The single consolidated entrypoint over :func:`verify_claim`: hand it a list
    of claim strings and get back one verdict dict per claim (the same shape
    ``verify_claim`` emits). Wraps the concurrency + per-claim isolation so no
    consumer re-rolls the ``asyncio.gather`` loop, and normalizes a failed claim
    to a canonical :class:`~core.agents.hallucination.enums.VerificationStatus`
    ``error`` verdict rather than sinking the whole batch.

    This is the coherence facade for the verification layer; the streaming
    orchestrator (:func:`~core.agents.hallucination.streaming.verify_response_streaming`)
    keeps its own per-claim timeout/SSE machinery, but new callers that just
    need "verify these N claims" use this.

    ``deadline``, ``stale_context``, ``source_artifact_ids``, and
    ``claim_context`` are threaded verbatim to every :func:`verify_claim` so
    batch callers (e.g. briefs generation) get the same deadline-bounding,
    stale-cutoff freshness routing, and anti-circularity the streaming path
    enforces — the facade must not be a bypass around those integrity gates.
    ``source_urls`` are extracted per-claim from inline citations (mirroring
    the streaming orchestrator) so a fabricated citation is NLI-entailed against
    the cited page before any KB / cross-model fallback runs.
    """
    if not claims:
        return []

    # Lazy import: streaming.py imports this module at load time, so the URL
    # extractor can only be pulled in at call time (both modules are fully
    # initialised by then). Mirrors the streaming per-claim call site.
    from core.agents.hallucination.streaming import _extract_source_urls_from_claim

    async def _one(claim: str) -> dict[str, Any]:
        try:
            return await verify_claim(
                claim,
                chroma_client,
                neo4j_driver,
                redis_client,
                threshold=threshold,
                model=model,
                streaming=streaming,
                expert_mode=expert_mode,
                source_artifact_ids=source_artifact_ids,
                response_context=response_context,
                claim_context=claim_context,
                conversation_context=conversation_context,
                source_urls=_extract_source_urls_from_claim(claim),
                deadline=deadline,
                stale_context=stale_context,
            )
        except Exception as exc:  # noqa: BLE001 — one bad claim must not sink the batch
            log_swallowed_error(f"{__name__}.verify_claims", exc)
            return {
                "claim": claim,
                "status": VerificationStatus.error.value,
                "confidence": 0.0,
                "reason": f"verifier error: {exc}",
                "verification_method": "error",
            }

    return await asyncio.gather(*[_one(c) for c in claims])


# ---------------------------------------------------------------------------
# verify_claim pipeline stages
# ---------------------------------------------------------------------------
# verify_claim is a staged pipeline: (1) cited-URL grounding →
# (2) KB/memory evidence gathering → (3) entailment + escalation → (4) verdict
# assembly. Stages 1-2 are cohesive enough to live as their own functions so
# the orchestrator reads as a sequence of named steps; stage 3's many
# escalation branches stay inline (each is a distinct terminal-verdict decision)
# and share the stage-4 verdict-assembly helpers (`_external_verdict` /
# `_escalated_external_verdict`) defined above.


async def _verify_via_cited_urls(
    claim: str,
    source_urls: list[str] | None,
    deadline: float | None,
) -> dict[str, Any] | None:
    """Cited-URL grounding stage (Task 12 / audit V-3).

    NLI-entail the claim against each LLM-cited page body *before* any KB lookup
    or cross-model web search — otherwise a fabricated citation ("According to
    https://wikipedia.org/foo, the sky is green") can get "confirmed" against an
    unrelated web-search result because the verifier re-searches from claim text
    and ignores the cited source.

    Bounded to at most 3 URLs per claim to cap latency, and deadline-gated (each
    check costs a fetch + NLI; three exceed the tightest per-claim budget). The
    first URL returning a definitive verdict (verified/unverified) wins; a URL
    that errors or returns "uncertain" falls through to the next. Returns the
    terminal verdict dict (``claim`` stamped) or ``None`` to fall through to the
    KB + external path.
    """
    for url in (source_urls or [])[:3]:
        _budget = _remaining_budget(deadline)
        if _budget is not None and _budget < _MIN_EXTERNAL_CALL_BUDGET_S * 2:
            break
        try:
            cited_verdict = await _verify_against_cited_url(claim, url)
        except Exception as exc:
            log_swallowed_error(f"{__name__}.cited_url_verify", exc)
            logger.debug("cited URL verification failed for %s: %s", url, exc)
            continue
        if cited_verdict.get("status") in ("verified", "unverified"):
            cited_verdict["claim"] = claim
            return cited_verdict
        # status == "uncertain" → try the next cited URL, then fall through to
        # KB / external if all URLs are inconclusive.
    return None


async def _gather_kb_evidence(
    claim: str,
    chroma_client: Any,
    *,
    response_context: str | None,
    source_artifact_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Evidence-gathering stage: assemble the KB + user-memory candidate set.

    Lightweight (vector + BM25) KB retrieval merged with the user-confirmed
    memory query, memory-authority boosted, min-relevance filtered, anti-circular
    penalized against ``source_artifact_ids``, relevance-sorted, and term-overlap
    sanity filtered. Returns the candidate evidence list (before recency
    weighting); the caller owns the no-evidence → external fallback decision and
    the freshness re-rank so those stay visible in the orchestrator.
    """
    from core.agents.query_agent import lightweight_kb_query

    # Exclude 'conversations' domain from general KB query to avoid
    # self-verification against feedback-ingested LLM responses.
    verification_domains = [d for d in config.DOMAINS if d != "conversations"]
    # Build an enriched query: the bare claim text is often too terse
    # for vector search (e.g. "it uses 768 dimensions" without context).
    # Prepending the response_context topic gives the embedding model
    # enough signal to retrieve relevant KB chunks.
    enriched_query = claim
    if response_context:
        # Use the topic summary (first line) — not the full multi-claim
        # enrichment which adds noise to the embedding.
        topic = response_context.split("\n")[0].strip()
        if topic and len(topic) > 10:
            enriched_query = f"{topic}: {claim}"
    # Use lightweight retrieval (vector + BM25 hybrid only) — skips graph
    # expansion, cross-encoder, quality boost, MMR, and context assembly
    # for significantly faster per-claim verification.
    kb_results = await lightweight_kb_query(
        query=enriched_query,
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
        mr["relevance"] = min(1.0, round(raw_rel + memory_authority_boost(mr), 4))
        all_results.append(mr)

    # Filter out results below verification relevance threshold
    all_results = [
        r for r in all_results
        if r.get("relevance", 0) >= config.VERIFICATION_MIN_RELEVANCE
    ]

    # --- Anti-circularity: penalise KB results that were injected into
    # the LLM prompt.  These cannot independently verify a claim because
    # the response was *derived* from them — matching is expected.
    # Penalty is 0.15 (not 0.30): the old 0.30 was too aggressive — it
    # pushed genuine supporting docs from 0.65 to 0.35, right at the
    # discard threshold, causing false "uncertain" verdicts when the KB
    # actually contained the answer.
    _CIRCULAR_PENALTY = 0.15
    if source_artifact_ids:
        _src_set = set(source_artifact_ids)
        for r in all_results:
            aid = r.get("artifact_id", "")
            if aid and aid in _src_set:
                original_rel = r.get("relevance", 0.0)
                r["_circular"] = True
                r["relevance"] = max(0.0, round(original_rel - _CIRCULAR_PENALTY, 4))
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

    return all_results


async def _score_kb_grounding(
    claim: str,
    top_results: list[dict[str, Any]],
    top_result: dict[str, Any],
    raw_similarity: float,
    neo4j_driver: Any,
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    """Grounding-signal stage — the three signals the verdict branches consume.

    Returns ``(similarity, details, nli)``:
    - ``similarity``: multi-result calibrated confidence, with the optional
      graph-corroboration boost applied when the top artifact has ≥2 verified
      graph neighbours;
    - ``details``: transparency / analytics metadata;
    - ``nli``: entailment/contradiction score of the claim against the top KB
      snippet (neutral fallback if scoring is unavailable).

    Pure with respect to control flow — every terminal-verdict decision made
    from these signals stays in the orchestrator.
    """
    # Apply multi-result confidence calibration
    similarity = _compute_adjusted_confidence(claim, top_results, raw_similarity)
    details = _build_verification_details(claim, top_results)

    # --- Graph-guided verification: connected verified artifacts boost confidence ---
    # If the source artifact has graph relationships to other verified artifacts,
    # this corroboration increases trust (knowledge graph structure as evidence).
    _graph_boost = getattr(config, "GRAPH_VERIFICATION_BOOST", 0.05)
    if _graph_boost > 0 and neo4j_driver and top_result.get("artifact_id"):
        try:
            with neo4j_driver.session() as _gs:
                _graph_count = _gs.run(
                    "MATCH (a:Artifact {id: $aid})-[:RELATES_TO|DEPENDS_ON|REFERENCES]-(b:Artifact) "
                    "WHERE EXISTS { MATCH (b)<-[:RELATES_TO]-(m:Memory)-[:VERIFIED_BY]->(r:VerificationReport) } "
                    "RETURN count(b) AS verified_neighbors",
                    aid=top_result["artifact_id"],
                ).single()
                if _graph_count and _graph_count["verified_neighbors"] >= 2:
                    similarity = min(1.0, similarity + _graph_boost)
                    details["graph_verified_neighbors"] = _graph_count["verified_neighbors"]
        except Exception as exc:  # noqa: BLE001 — graph boost is non-blocking
            log_swallowed_error(__name__, exc)

    # --- NLI entailment check on top KB result ---
    # v0.93.10: switched to nli_score_async so N concurrent
    # verify_claim() tasks dispatched via asyncio.gather rendezvous
    # in a single batched inference (`_NliBatcher` coalesces calls
    # within an NLI_COALESCE_MS window).  Pre-v0.93.10 these calls
    # serialised on the ONNX-session lock — N concurrent claims took
    # N × per-call time.  Now they take ~1 × per-batch time.
    # Phase 3.1: the single grounding choke point routes through the pluggable
    # verifier tier, and the premise widens from the old [:512]-char slice to
    # NLI_PREMISE_CHAR_LIMIT. The 512-char cut was a second, ~4× tighter ceiling
    # stacked on the tokenizer's own 512-*token* truncation — the model never saw
    # the ~2k chars (≈512 tokens) it was built to read.
    try:
        nli = await get_grounding_verifier().score(
            top_result.get("content", "")[:NLI_PREMISE_CHAR_LIMIT], claim
        )
    except Exception as exc:
        log_swallowed_error('core.agents.hallucination.verification', exc)
        logger.debug("NLI scoring failed for claim %r — falling back to similarity", claim[:60])
        nli = {"entailment": 0.0, "contradiction": 0.0, "neutral": 1.0, "label": "neutral"}

    return similarity, details, nli


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
    conversation_context: list[dict[str, str]] | None = None,
    source_urls: list[str] | None = None,
    deadline: float | None = None,
    stale_context: bool = False,
) -> dict[str, Any]:
    """Verify a single claim against the knowledge base and user memories.

    ``deadline`` (a ``time.monotonic()`` timestamp) bounds every external
    call and cited-URL fetch inside this claim so the total work fits the
    caller's per-claim ``wait_for`` window instead of overrunning it.

    ``stale_context`` marks a claim extracted from a response that admitted
    a stale knowledge cutoff ("as of my knowledge cutoff…"). The framing is
    stripped from individual claims at extraction time, so the caller must
    propagate it — such claims are treated as temporal: external fallbacks
    force web search and KB entailment triggers the freshness gates.

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
    if threshold is None:
        threshold = config.HALLUCINATION_THRESHOLD
    unverified_threshold = config.HALLUCINATION_UNVERIFIED_THRESHOLD
    ext_kb_threshold = config.EXTERNAL_VERIFY_KB_THRESHOLD

    # Phase 3.2 — trust-or-escalate policy seam. Built with the thresholds this
    # call resolved so every escalation decision reads from one named, testable
    # place instead of duplicated inline predicates + bare literals. The default
    # policy reproduces this function's pre-3.2 decisions exactly.
    policy = get_escalation_policy(verified_threshold=threshold)
    # Claim currency is grounding-independent (claim text + stale flag) — decide
    # once and reuse across Fallback 1 and every positive-grounding branch.
    _is_temporal = policy.is_temporal(claim, stale_context=stale_context)

    # Phase 3.4 — freshness classification (pure, no LLM call). Reused below
    # to (a) prefer newer KB evidence when the claim is time-sensitive, and
    # (b) cap the verdict-cache TTL regardless of which verification method
    # ultimately answers the claim.
    claim_freshness = classify_claim_freshness(claim)

    # --- Fact-level cache: skip re-verification for previously seen claims ---
    # Key on (claim, model, method, response_context) so we don't return a
    # stale verdict from a different model or from a prior conversation
    # where a pronoun in `claim` resolved to a different subject.
    #
    # `method` here is the verification *tier* the caller is running, not the
    # method-of-record on the verdict. We use "expert" vs "standard" so a
    # user who explicitly requested expert-mode verification doesn't get a
    # cheaper-mode verdict back — the writer below uses the same tier.
    _cache_tier = "expert" if expert_mode else "standard"
    cached = await get_cached_verdict(
        redis_client,
        claim,
        model=model or "",
        method=_cache_tier,
        response_context=response_context or "",
    )
    if cached and cached.get("status") in ("verified", "unverified", "uncertain"):
        logger.info("Claim cache hit: '%s' -> %s", claim[:50], cached["status"])
        return {
            **cached,
            "claim": claim,
            "cached": True,
        }

    # Common kwargs for all _verify_claim_externally calls in this function.
    # Conversation context is threaded through to expert mode verification.
    _ext_common: dict[str, Any] = {
        "streaming": streaming,
        "expert_mode": expert_mode,
        "response_context": response_context,
        "claim_context": claim_context,
        "conversation_context": conversation_context if expert_mode else None,
        "deadline": deadline,
    }

    async def _cache_result(result: dict[str, Any]) -> dict[str, Any]:
        """Cache the verdict (fire-and-forget) and return the result unchanged.

        Uncertain claims are also cached (with shorter TTL) to prevent
        repeated API calls for claims that are genuinely unverifiable.
        Transport-failure verdicts are NOT cached — a 402/breaker/timeout
        "uncertain" is an outage artifact, not a judgment, and caching one
        keeps serving it after the outage passes (2026-07-10: a credit
        exhaustion poisoned V-06/V-94/EX-02/JV-05 across eval runs).
        """
        status = result.get("status")
        if status in ("verified", "unverified", "uncertain") and not _is_transport_failure_verdict(result):
            # Key on the same (model, tier, response_context) the reader uses
            # above — otherwise cache writes never match cache reads.
            #
            # Phase 3.4 — time-sensitive claims get the short cache cap
            # regardless of *which* verification method produced this
            # verdict (kb / kb_nli / cross_model / web_search). Previously
            # only web_search-method verdicts were capped (still enforced
            # independently inside cache_verdict); this closes the gap for
            # a time-sensitive claim that happened to resolve via KB or
            # cross-model verification.
            _cache_ttl_kwargs: dict[str, int] = {}
            if claim_freshness == ClaimFreshness.TIME_SENSITIVE:
                _cache_ttl_kwargs["ttl"] = TIME_SENSITIVE_VERDICT_TTL_S
            await cache_verdict(
                redis_client,
                claim,
                result,
                response_context=response_context,
                model=model or "",
                method=_cache_tier,
                **_cache_ttl_kwargs,
            )
        return result

    try:
        # Phase 3.5 — type-aware routing. Evasion and ignorance claims assert
        # something about the model's *stance* ("I don't have information about
        # X"; a hedge), not a fact the KB can confirm. Grading their literal text
        # against KB similarity rubber-stamps a hedge whose surface content
        # happens to be grounded — the calibration study's evasion=0.375 /
        # ignorance=0.727 misgrades (4/14 verified at ~1.0). Route them straight
        # to the type-aware external verifier, which inverts the verdict on
        # whether the underlying facts actually exist (see _invert_evasion_verdict
        # / _invert_ignorance_verdict), bypassing the KB-grounding path entirely.
        if policy.type_route(claim) is EscalationTier.WEB:
            ext_result = await _verify_claim_externally(
                claim, model, force_web_search=True, **_ext_common,
            )
            return await _cache_result(_external_verdict(claim, ext_result))

        # Stage 1 — cited-URL grounding: NLI-entail the claim against any
        # LLM-cited page bodies before any KB / cross-model fallback.
        cited_verdict = await _verify_via_cited_urls(claim, source_urls, deadline)
        if cited_verdict is not None:
            return await _cache_result(cited_verdict)

        # Stage 2 — evidence gathering: KB + user-memory candidate set,
        # boosted / filtered / anti-circular-penalized / term-overlap sanitized.
        all_results = await _gather_kb_evidence(
            claim,
            chroma_client,
            response_context=response_context,
            source_artifact_ids=source_artifact_ids,
        )

        # --- Fallback 1: No KB results at all → try external verification ---
        # Only force web search for claims that genuinely need current data.
        # Historical/established facts (pre-2024) can be verified via cross-model.
        if not all_results:
            ext_result = await _verify_claim_externally(
                claim, model, force_web_search=_is_temporal, **_ext_common,
            )
            return await _cache_result(_external_verdict(claim, ext_result))

        # Phase 3.4 — for time-sensitive claims, prefer newer evidence among
        # near-equally-relevant KB results before picking the top match. A
        # bounded re-rank (see freshness.weight_evidence_by_recency), not a
        # rewrite of selection: dated/timeless claims and non-time-sensitive
        # cases fall through unchanged.
        all_results = weight_evidence_by_recency(all_results, claim_freshness)

        top_results = all_results[:3]
        top_result = top_results[0]
        raw_similarity = top_result.get("relevance", 0.0)
        # Use pre-boost relevance for escalation if this is a memory result,
        # so the +0.15 authority boost doesn't mask low KB evidence.
        escalation_similarity = top_result.get("_raw_relevance", raw_similarity)

        # Extract top KB snippet to pass as partial evidence to external
        # verification. Standard mode truncates to 300 chars for token economy
        # with gpt-4o-mini. Expert mode widens to 1200 chars — the premium
        # verifier (Grok 4) has a much larger effective context and benefits
        # from the extra triangulation surface, especially on technical claims
        # where the snippet's full paragraph matters.
        _snippet_limit = 1200 if expert_mode else 300
        _top_snippet = (top_result.get("content", "") or "")[:_snippet_limit] or None

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
                claim, model,
                **_ext_common, kb_snippet=_top_snippet,
            )
            return await _cache_result(
                _external_verdict(claim, ext_result, circular_source=True)
            )

        # --- Fallback 2: Very low KB similarity → try external verification ---
        if escalation_similarity < ext_kb_threshold:
            ext_result = await _verify_claim_externally(
                claim, model,
                **_ext_common, kb_snippet=_top_snippet,
            )
            # Use external result if it provides a stronger signal than KB
            if ext_result["confidence"] > raw_similarity:
                return await _cache_result(_external_verdict(claim, ext_result))

        # Stage 3 — grounding signals: calibrated similarity (+ graph boost),
        # transparency details, and the NLI entailment/contradiction score that
        # the verdict branches below decide on.
        similarity, details, _nli = await _score_kb_grounding(
            claim, top_results, top_result, raw_similarity, neo4j_driver,
        )
        # Grounding signals the escalation policy decides on (Phase 3.2).
        _signals = GroundingSignals(
            similarity=similarity,
            raw_similarity=raw_similarity,
            entailment=_nli["entailment"],
            contradiction=_nli["contradiction"],
        )

        if _nli["entailment"] >= config.NLI_ENTAILMENT_THRESHOLD:
            # For recency/current-event claims, even strong NLI entailment needs
            # web search validation — KB evidence may be semantically correct but
            # stale. The policy resolves strong-entailment grounding to WEB (only)
            # when the claim is time-sensitive, else TRUST_KB.
            if policy.classify(_signals, is_temporal=_is_temporal) is EscalationTier.WEB:
                logger.debug(
                    "NLI entailed but temporal claim — verifying freshness via web search: %r",
                    claim[:60],
                )
                ext_result = await _verify_claim_externally(
                    claim, model, force_web_search=True,
                    **_ext_common, kb_snippet=_top_snippet,
                )
                if ext_result and ext_result.get("status") in ("verified", "unverified"):
                    return await _cache_result(
                        _escalated_external_verdict(claim, ext_result)
                    )
                # If web search inconclusive, fall to the staleness gate below
                # (Phase 4.2) before rubber-stamping NLI-entailed stale data.

            # Phase 4.2 — stale-evidence gate. A temporal claim whose only
            # support is KB evidence older than the verification window, with
            # live verification inconclusive, must NOT come back "verified":
            # NLI entailment confirms the text matches, not that the value is
            # current. Downgrade to uncertain/stale_evidence. Non-temporal
            # claims and fresh evidence keep the verified verdict.
            if _is_temporal and _evidence_is_stale(
                top_result, config.VERIFICATION_STALENESS_WINDOW_DAYS
            ):
                return await _cache_result(
                    _stale_evidence_verdict(claim, top_result, similarity)
                )

            return await _cache_result({
                "claim": claim,
                "status": "verified",
                "similarity": round(similarity, 3),
                "nli_entailment": _nli["entailment"],
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "memory_source": bool(top_result.get("memory_source")),
                "verification_details": details,
                "verification_method": "kb_nli",
                **({"circular_source": True} if top_result.get("_circular") else {}),
            })

        if _nli["contradiction"] >= config.NLI_CONTRADICTION_THRESHOLD:
            # KB-authority gate: only treat NLI contradiction as terminal when
            # the KB source is strong enough to trust. TWO signals must agree:
            #   (1) raw_similarity >= threshold — topically related to the claim
            #   (2) NLI entailment >= 0.15 — semantically about the claim's
            #       assertion, not just keyword-overlapping on an orthogonal
            #       topic (e.g. a chat transcript mentioning "Paris" in a
            #       different context scores high similarity but ~0 entailment)
            # High contradiction + near-zero entailment is the signature of
            # "different topic, same keywords" — NOT a real contradiction.
            # Escalate instead of hard-failing. The policy encodes the two-signal
            # KB-authority gate (topical strength AND semantic alignment).
            if not policy.kb_contradiction_authoritative(_signals):
                logger.debug(
                    "NLI contradiction on weak KB evidence (sim=%.2f < %.2f) — escalating externally: %r",
                    raw_similarity, threshold, claim[:60],
                )
                ext_result = await _verify_claim_externally(
                    claim, model, **_ext_common, kb_snippet=_top_snippet,
                )
                if ext_result and ext_result.get("status") in ("verified", "unverified", "uncertain"):
                    return await _cache_result(
                        _escalated_external_verdict(
                            claim,
                            ext_result,
                            # Preserve the NLI signal for observability / debugging
                            kb_nli_contradiction=round(_nli["contradiction"], 3),
                            kb_nli_escalated=True,
                        )
                    )
                # If external verification failed/errored, fall through to the
                # original terminal-contradiction verdict below as a safety net.

            # Persist to the contradiction ledger (Wiki contradiction surface +
            # weekly synthesis). Gated + best-effort; the sink is wired from app
            # startup because core/ cannot import app.services.contradiction_log.
            if config.ENABLE_CONTRADICTION_LEDGER:
                from core.agents.hallucination.contradiction_sink import (
                    get_contradiction_sink,
                )

                _csink = get_contradiction_sink()
                if _csink is not None:
                    try:
                        await _csink(
                            claim_text=claim,
                            source_text=top_result.get("content", "")[:500],
                            source_artifact_id=top_result.get("artifact_id", ""),
                            severity="high",
                        )
                    except Exception as exc:  # noqa: BLE001 — ledger write must not block verification
                        log_swallowed_error(
                            "core.agents.hallucination.verification.contradiction_sink",
                            exc,
                        )

            return await _cache_result({
                "claim": claim,
                "status": "unverified",
                "similarity": round(similarity, 3),
                "nli_contradiction": _nli["contradiction"],
                "reason": "KB evidence contradicts claim",
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "verification_details": details,
                "verification_method": "kb_nli",
                **({"circular_source": True} if top_result.get("_circular") else {}),
            })

        if similarity >= threshold:
            # NLI neutral/weak — the policy resolves this positive-grounding case
            # to WEB (time-sensitive) then CROSS_MODEL (similarity high but
            # entailment below the alignment floor) in sequence, else TRUST_KB.
            # Recency/current-event claims MUST escalate to web search even when
            # KB similarity is high — stale data can match with high similarity.
            if _is_temporal:
                # Force web search for temporal claims despite high KB similarity
                logger.debug(
                    "NLI neutral + high similarity but temporal claim — escalating to web search: %r",
                    claim[:60],
                )
                ext_result = await _verify_claim_externally(
                    claim, model, force_web_search=True,
                    **_ext_common, kb_snippet=_top_snippet,
                )
                if ext_result and ext_result.get("status") in ("verified", "unverified"):
                    return await _cache_result(
                        _escalated_external_verdict(claim, ext_result)
                    )
                # If web search was inconclusive, fall through to KB verdict below

            # Semantic-alignment gate: high similarity + fully-neutral NLI is
            # the signature of "shared keywords, different topic" (e.g. a KB
            # doc mentioning both 'Docker' and 'Java' gets high similarity to
            # the false claim 'Docker was written in Java' but NLI says
            # neutral — the doc isn't actually supporting the claim). Require
            # a minimum entailment floor to trust the kb-only verdict. If NLI
            # is entirely indifferent, escalate externally rather than rubber-
            # stamping on keyword overlap alone.
            if not policy.semantic_alignment_ok(_signals):
                logger.debug(
                    "High KB similarity (%.2f) but NLI entailment too weak "
                    "(%.2f) — escalating externally: %r",
                    similarity, _nli["entailment"], claim[:60],
                )
                ext_result = await _verify_claim_externally(
                    claim, model, **_ext_common, kb_snippet=_top_snippet,
                )
                if ext_result and ext_result.get("status") in ("verified", "unverified", "uncertain"):
                    return await _cache_result(
                        _escalated_external_verdict(
                            claim,
                            ext_result,
                            kb_semantic_gate_escalated=True,
                            kb_similarity=round(similarity, 3),
                            kb_nli_entailment=round(_nli["entailment"], 3),
                        )
                    )
                # External inconclusive — fall through to the kb verdict below
                # as a safety net so we still return something.

            # Phase 4.2 — stale-evidence gate (mirror of the kb_nli path). A
            # temporal claim resting on KB evidence older than the verification
            # window, with live verification inconclusive, is downgraded to
            # uncertain/stale_evidence rather than "verified" on stale data.
            if _is_temporal and _evidence_is_stale(
                top_result, config.VERIFICATION_STALENESS_WINDOW_DAYS
            ):
                return await _cache_result(
                    _stale_evidence_verdict(claim, top_result, similarity)
                )

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
                claim, model,
                **_ext_common, kb_snippet=_top_snippet,
            )
            if ext_result.get("status") in ("verified", "unverified"):
                return await _cache_result(_external_verdict(claim, ext_result))
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
                claim, model,
                **_ext_common, kb_snippet=_top_snippet,
            )
            if ext_result.get("status") in ("verified", "unverified"):
                return await _cache_result(_external_verdict(claim, ext_result))
            # External also uncertain — try web search as final escalation.
            # Previously skipped in streaming mode, but 26% uncertain rate was
            # too high. Web search resolves many "cannot independently verify"
            # cases that cross-model LLMs fail on.
            if ext_result.get("verification_method") != "web_search":
                web_result = await _verify_claim_externally(
                    claim, model, force_web_search=True,
                    **_ext_common, kb_snippet=_top_snippet,
                )
                if web_result.get("status") in ("verified", "unverified"):
                    return await _cache_result(_external_verdict(claim, web_result))
            # --- Fallback 5: Escalate to authoritative verification ---
            # Before giving up, try authoritative external sources if available.
            # This catches claims that cross-model LLMs can't verify from
            # parametric knowledge but authoritative data sources can.
            if getattr(config, "EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", False):
                try:
                    from core.agents.hallucination.authoritative_verify import (
                        verify_claim_authoritatively,
                    )

                    auth = await verify_claim_authoritatively(
                        claim,
                        kb_results=[{"content": _top_snippet}] if _top_snippet else None,
                    )
                    auth_sources = auth.get("authoritative_sources", [])
                    # If any authoritative source has strong entailment, upgrade to verified
                    strong_support = [s for s in auth_sources if s.get("nli_entailment", 0) >= config.NLI_ENTAILMENT_THRESHOLD]
                    strong_contradict = [s for s in auth_sources if s.get("nli_contradiction", 0) >= config.NLI_CONTRADICTION_THRESHOLD]
                    # Full structured evidence for downstream audit/UI — previously
                    # only the scalar verdict + source_urls survived, losing
                    # per-source NLI scores, domain classification, and
                    # KB-vs-external cross-validation agreement.
                    _auth_payload = {
                        "authoritative_sources": auth_sources,
                        "claim_domain": auth.get("claim_domain", "general"),
                        "cross_validation": auth.get("cross_validation", {}),
                        "evidence_summary": auth.get("evidence_summary", ""),
                    }
                    if strong_support:
                        return await _cache_result({
                            "claim": claim,
                            "status": "verified",
                            "similarity": max(s["nli_entailment"] for s in strong_support),
                            "reason": f"Authoritative source confirmed: {strong_support[0].get('source', 'external')}",
                            "verification_method": "authoritative",
                            "source_urls": [s.get("source_url", "") for s in strong_support if s.get("source_url")],
                            **_auth_payload,
                            **_kb_source_fields(top_result),
                        })
                    if strong_contradict:
                        return await _cache_result({
                            "claim": claim,
                            "status": "unverified",
                            "similarity": 0.35,
                            "reason": f"Authoritative source contradicts: {strong_contradict[0].get('source', 'external')}",
                            "verification_method": "authoritative",
                            "source_urls": [s.get("source_url", "") for s in strong_contradict if s.get("source_url")],
                            **_auth_payload,
                            **_kb_source_fields(top_result),
                        })
                except Exception as exc:  # noqa: BLE001 — authoritative escalation is best-effort
                    log_swallowed_error(__name__, exc)

            # All methods exhausted — return uncertain with all available context
            return await _cache_result({
                "claim": claim,
                "status": "uncertain",
                "similarity": round(similarity, 3),
                "reason": details.get("reason", "Partial match — review recommended"),
                "verification_details": details,
                "verification_method": "kb",
                "source_urls": ext_result.get("source_urls", []),
                **_kb_source_fields(top_result),
            })

    except Exception as e:
        log_swallowed_error('core.agents.hallucination.verification', e)
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
        log_swallowed_error('core.agents.hallucination.verification', e)
        logger.warning("Consistency check failed: %s", e)
        return []
