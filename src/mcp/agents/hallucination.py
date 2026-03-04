# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination Detection Agent — cross-references LLM responses against the KB.

Verification is evidence-based (embedding similarity + numeric alignment),
not LLM opinion. This avoids the trap of a verifier hallucinating agreement
with the main model. All signals are grounded in document evidence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

import config
from middleware.request_id import tracing_headers
from utils.circuit_breaker import CircuitOpenError, get_breaker
from utils.llm_parsing import parse_llm_json
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.hallucination")

# ---------------------------------------------------------------------------
# Constants — configurable via env vars in config/settings.py
# ---------------------------------------------------------------------------
REDIS_HALLUCINATION_PREFIX = "hall:"
REDIS_HALLUCINATION_TTL = 86400 * 7  # 7 days

# Concurrency gate for external verification — defense-in-depth against
# bursts even on high-RPM models.  Lazily initialised because asyncio
# requires a running event loop for Semaphore().
_ext_verify_semaphore: asyncio.Semaphore | None = None


def _get_ext_verify_semaphore() -> asyncio.Semaphore:
    """Return (and lazily create) the external-verification semaphore."""
    global _ext_verify_semaphore
    if _ext_verify_semaphore is None:
        _ext_verify_semaphore = asyncio.Semaphore(config.EXTERNAL_VERIFY_MAX_CONCURRENT)
    return _ext_verify_semaphore


# Memory types that count as user-confirmed facts for verification
_MEMORY_TYPES = ["fact", "decision", "preference", "action_item"]

# Authority boost for memory-sourced results (user-confirmed content)
_MEMORY_AUTHORITY_BOOST = 0.05

# Sentence-ending pattern for heuristic extraction
_SENTENCE_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z])"
)

# Patterns that indicate a sentence contains a verifiable factual claim
_FACTUAL_PATTERNS = [
    re.compile(r"\b\d{4}\b"),                         # years
    re.compile(r"\b\d+(?:\.\d+)?%"),                   # percentages
    re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b"),      # numbers
    re.compile(r"\b(?:is|are|was|were|has|have|had)\b", re.I),  # state verbs
    re.compile(r"\b(?:released|created|founded|introduced|built|developed)\b", re.I),
    re.compile(r"\b(?:supports?|requires?|includes?|provides?|uses?|contains?)\b", re.I),
    re.compile(r"\b(?:version|v\d)\b", re.I),          # version references
    # Comparisons
    re.compile(r"\b(?:faster|slower|better|worse|larger|smaller|more|fewer|higher|lower)\s+than\b", re.I),
    # Causal language
    re.compile(r"\b(?:because|due to|caused by|leads to|results in|as a result)\b", re.I),
    # Attributions
    re.compile(r"\b(?:according to|reported by|published by|developed by|created by|authored by)\b", re.I),
    # Quantified claims
    re.compile(r"\b(?:up to|at least|approximately|roughly|about|around|over|under)\s+\d", re.I),
    # Capability claims
    re.compile(r"\b(?:by default|natively|out of the box|built[- ]in)\b", re.I),
]

# Sentences matching these are likely NOT factual claims
_NON_FACTUAL_PATTERNS = [
    re.compile(r"^\s*(?:I |you |let me|sure|okay|here|note|please|thanks|hello|hi )", re.I),
    re.compile(r"^\s*(?:would you|can I|shall I|do you want)", re.I),
    re.compile(r"^\s*```"),                             # code blocks
    re.compile(r"^\s*(?:for example|e\.g\.|i\.e\.)\s*$", re.I),   # standalone example markers
    re.compile(r"^\s*\d+\.\s*$"),                                  # bare numbered list markers
    re.compile(r"^\s*(?:import |from \w+ import |def |class |const |let |var |function )", re.I),  # code
    re.compile(r"^\s*(?:hope this helps|feel free|happy to help|let me know)", re.I),  # pleasantries
]

# Strong-signal patterns — a single match counts as 2 for scoring purposes
_STRONG_FACTUAL_PATTERNS = [
    re.compile(r"\b(?:faster|slower|better|worse|larger|smaller|more|fewer)\s+than\b", re.I),
    re.compile(r"\b(?:according to|reported by|published by)\b", re.I),
    re.compile(r"\b\d{4}\b.*\b(?:released|created|founded|introduced|launched)\b", re.I),
]

# Patterns that indicate a claim is about current events or recent information.
# These claims benefit from web-search-enabled verification (Grok + web search).
_CURRENT_EVENT_PATTERNS = [
    re.compile(r"\b(?:202[4-9]|203\d)\b"),                      # recent/future years
    re.compile(r"\b(?:recently|just|newly|latest|current|now)\b", re.I),
    re.compile(r"\b(?:announced|launched|released|unveiled|introduced|updated)\b.*\b(?:202[4-9]|this year|last (?:month|week|year))\b", re.I),
    re.compile(r"\b(?:this year|last year|this month|last month|this week|last week)\b", re.I),
    re.compile(r"\b(?:as of|since|starting|beginning|effective)\s+(?:202[4-9]|January|February|March|April|May|June|July|August|September|October|November|December)\b", re.I),
    re.compile(r"\b(?:trending|breaking|emerging|ongoing)\b", re.I),
    re.compile(r"\b(?:CEO|president|prime minister|acquired|merger|IPO|bankruptcy|election)\b", re.I),
    re.compile(r"\b(?:version|v)\s*\d+\.\d+.*\b(?:released|launched|available|shipped)\b", re.I),
]

# Patterns that indicate a verification model's response admits stale knowledge.
# When detected alongside a "supported" verdict for a current-event claim, the
# claim should be escalated to a web-search model for a second opinion.
_STALE_KNOWLEDGE_PATTERNS = [
    re.compile(r"as of (?:my|the) (?:last |latest )?(?:training|knowledge|data)", re.I),
    re.compile(r"(?:I |my )(?:training|knowledge) (?:cutoff|cut-off|only goes|ends|stops)", re.I),
    re.compile(r"I (?:don'?t|do not) have (?:information|data|knowledge) (?:after|beyond|past)", re.I),
    re.compile(r"(?:may have|might have|could have) changed since", re.I),
    re.compile(r"(?:unable to|cannot) (?:verify|confirm|access) (?:current|recent|latest)", re.I),
    re.compile(r"(?:not aware of|no information about) (?:any )?(?:recent|latest|current)", re.I),
]


def _has_staleness_indicators(text: str) -> bool:
    """Detect if a verification response admits knowledge staleness."""
    return any(p.search(text) for p in _STALE_KNOWLEDGE_PATTERNS)


# Patterns that detect when a claim is an *admission of ignorance* by the
# generating model rather than a positive factual assertion.  E.g.:
#   "I don't have information about the big beautiful bill passed in 2025"
#   "As of my last update, there is no specific information about X"
# These claims need special handling: instead of verifying the model's
# honesty about its limitations, we verify the UNDERLYING FACT and mark
# the response as refuted if the fact actually exists.
_IGNORANCE_ADMISSION_PATTERNS = [
    # "I don't/do not have (specific/any) information/data/knowledge about X"
    re.compile(
        r"I (?:don'?t|do not) have (?:(?:specific|any|detailed|enough) )?"
        r"(?:information|data|knowledge|details) (?:about|on|regarding)",
        re.I,
    ),
    # "I would not have that information"
    re.compile(
        r"I (?:would|wouldn'?t) not have (?:that |this |the |such )?"
        r"(?:information|data|knowledge)",
        re.I,
    ),
    # "there is no (specific) information about X"
    re.compile(
        r"there (?:is|was) no (?:specific |detailed |available |reliable )?"
        r"(?:information|data|evidence|record) (?:about|on|regarding|of)",
        re.I,
    ),
    # "I'm not aware of X" / "I am not aware"
    re.compile(r"I(?:'m| am) not (?:aware|certain|sure) (?:of|about|whether|if)", re.I),
    # "beyond/outside/after my knowledge/training cutoff"
    re.compile(
        r"(?:beyond|outside|after|past) (?:my|the) (?:knowledge|training) "
        r"(?:cutoff|cut-off|date|window)",
        re.I,
    ),
    # "I cannot confirm/verify/provide information about"
    re.compile(
        r"I (?:cannot|can'?t|could not|couldn'?t) "
        r"(?:confirm|verify|provide (?:specific )?(?:information|details)|access "
        r"(?:information|data)) (?:about|on|regarding|for|whether|if)",
        re.I,
    ),
    # "my training data does not include/contain/cover"
    re.compile(
        r"my (?:training(?: data| set)?|knowledge(?: base)?|data) "
        r"(?:does not|doesn'?t|may not) (?:include|contain|cover|extend to)",
        re.I,
    ),
    # "As of my last update... no/not/there is no"
    re.compile(
        r"as of my (?:last |latest |most recent )?(?:update|training|knowledge)"
        r".*?(?:no |not |there (?:is|was) no |I (?:don'?t|do not) )",
        re.I,
    ),
]


def _is_ignorance_admission(claim: str) -> bool:
    """Detect whether a claim is an admission of ignorance by the generating model.

    Returns True if the claim contains language indicating the model doesn't
    have information about a topic, rather than making a positive factual
    assertion.  Such claims need special verification: instead of checking
    whether the model is being honest, we check whether the underlying facts
    actually exist.
    """
    return any(p.search(claim) for p in _IGNORANCE_ADMISSION_PATTERNS)


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
        # Strip the generic prefix and prepend our own.
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


# ---------------------------------------------------------------------------
# System prompts for verification LLM calls
# ---------------------------------------------------------------------------

_SYSTEM_CLAIM_EXTRACTION = (
    "You are a factual claim extraction engine for a verification system. "
    "Your job is to identify every verifiable factual statement in the text. "
    "A verifiable claim is any statement that could be checked against "
    "external sources — including dates, numbers, statistics, named entities, "
    "causal relationships, comparisons, attributions, and technical specifications. "
    "Do NOT extract opinions, greetings, questions, or code examples. "
    "Return ONLY a JSON array of objects, each with:\n"
    '  {"claim": "<the factual statement>", "type": "<category>"}\n'
    "Valid types: date, statistic, attribution, comparison, technical, "
    "definition, causal, general.\n"
    "If the text contains no verifiable claims, return []."
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
    "Search the web for authoritative sources to confirm or refute the claim. "
    "Prioritize official sources, reputable news outlets, and primary documents.\n\n"
    "Respond with ONLY a JSON object — no other text:\n"
    '{"verdict": "supported"|"refuted"|"insufficient_info", '
    '"confidence": 0.0-1.0, '
    '"reasoning": "1-2 sentence explanation with source reference"}\n\n'
    "Rules:\n"
    "- \"supported\": Web sources confirm the claim is accurate\n"
    "- \"refuted\": Web sources show the claim contains factual errors\n"
    "- \"insufficient_info\": Cannot find reliable sources to verify\n"
    "- Always cite the source in your reasoning (e.g., \"per Reuters...\", "
    "\"according to the official docs...\")\n"
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
    "provide.\n\n"
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

# Numeric extraction patterns for contradiction detection
_NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b")
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_PERCENT_RE = re.compile(r"\b(\d+(?:\.\d+)?%)")


# ---------------------------------------------------------------------------
# Heuristic claim extraction (fallback when LLM extraction fails)
# ---------------------------------------------------------------------------

def _extract_claims_heuristic(response_text: str) -> list[str]:
    """Extract likely factual sentences using regex patterns.

    Used as a fallback when LLM-based extraction fails or returns empty.
    Identifies sentences containing numbers, dates, version references,
    comparisons, causal language, attributions, and state verbs — then
    filters out greetings, code, and meta-commentary.
    """
    max_claims = config.HALLUCINATION_MAX_CLAIMS

    text = response_text[:5000].strip()

    # Strip markdown formatting but preserve content
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)        # headers
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)       # bullet markers
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)      # numbered list markers
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                      # bold
    text = re.sub(r"\*(.+?)\*", r"\1", text)                          # italic
    text = re.sub(r"`([^`]+)`", r"\1", text)                          # inline code

    # Remove multi-line code blocks entirely
    text = re.sub(r"```[\s\S]*?```", "", text)

    sentences = _SENTENCE_RE.split(text)
    # Also split on newlines that look like separate statements
    expanded: list[str] = []
    for s in sentences:
        expanded.extend(line.strip() for line in s.split("\n") if line.strip())

    claims: list[str] = []
    for sentence in expanded:
        # Skip very short or very long sentences
        if len(sentence) < 20 or len(sentence) > 300:
            continue

        # Skip non-factual patterns
        if any(p.search(sentence) for p in _NON_FACTUAL_PATTERNS):
            continue

        # Check for factual indicators
        factual_score = sum(1 for p in _FACTUAL_PATTERNS if p.search(sentence))

        # Strong-signal patterns provide a bonus (single strong match + 1 base = qualifies)
        strong_bonus = sum(1 for p in _STRONG_FACTUAL_PATTERNS if p.search(sentence))
        effective_score = factual_score + strong_bonus

        if effective_score >= 2:
            # Clean up trailing punctuation for consistency
            claim = sentence.rstrip(".,;: ")
            if claim and claim not in claims:
                claims.append(claim)
                if len(claims) >= max_claims:
                    break

    return claims


# ---------------------------------------------------------------------------
# LLM-based claim extraction
# ---------------------------------------------------------------------------

async def extract_claims(response_text: str) -> tuple[list[str], str]:
    """Extract factual claims from an LLM response.

    Returns a tuple of (claims, method) where method is one of:
    - "llm": claims extracted via LLM
    - "heuristic": claims extracted via regex fallback
    - "none": no claims could be extracted
    """
    min_length = config.HALLUCINATION_MIN_RESPONSE_LENGTH
    max_claims = config.HALLUCINATION_MAX_CLAIMS

    if len(response_text) < min_length:
        return [], "none"

    # Try LLM extraction first
    llm_claims = await _extract_claims_llm(response_text, max_claims)
    if llm_claims:
        return llm_claims, "llm"

    # Fallback to heuristic extraction
    logger.info("LLM claim extraction returned empty — falling back to heuristic")
    heuristic_claims = _extract_claims_heuristic(response_text)
    if heuristic_claims:
        return heuristic_claims, "heuristic"

    return [], "none"


async def _extract_claims_llm(response_text: str, max_claims: int) -> list[str]:
    """Extract factual claims using the verification LLM model."""
    user_prompt = (
        f"Extract up to {max_claims} verifiable factual claims from the text below.\n"
        "Include: dates, statistics, comparisons (X is faster/better than Y), "
        "causal statements (because, due to, leads to), attributions "
        "(according to, created by), technical specs, and definitions.\n"
        "Exclude: opinions, greetings, questions, code blocks, and meta-commentary.\n"
        "For list items that contain facts, extract the factual part as a standalone claim.\n\n"
        f"Text:\n{response_text[:5000]}\n\n"
        "JSON array:"
    )

    breaker = get_breaker("bifrost-claims")

    async def _bifrost_extract() -> dict:
        async with httpx.AsyncClient(timeout=config.BIFROST_TIMEOUT, headers=tracing_headers()) as client:
            resp = await client.post(
                f"{config.BIFROST_URL}/chat/completions",
                json={
                    "model": config.VERIFICATION_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_CLAIM_EXTRACTION},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1200,
                },
            )
            resp.raise_for_status()
            return resp.json()

    try:
        data = await breaker.call(_bifrost_extract)
        content = data["choices"][0]["message"]["content"].strip()
        raw = parse_llm_json(content)
        if isinstance(raw, list):
            # Handle both plain strings and structured {"claim": ..., "type": ...}
            claims: list[str] = []
            for item in raw[:max_claims]:
                if isinstance(item, dict):
                    claims.append(str(item.get("claim", item.get("text", str(item)))))
                else:
                    claims.append(str(item))
            return claims
        logger.warning("LLM claim extraction returned non-list: %s", type(raw).__name__)
    except CircuitOpenError:
        logger.warning("Bifrost claims circuit open, skipping LLM claim extraction")
    except httpx.HTTPStatusError as e:
        logger.warning("Claim extraction HTTP error: %s %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("Claim extraction failed: %s", e)

    return []


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

    claim_years = set(_YEAR_RE.findall(claim))
    claim_pcts = set(_PERCENT_RE.findall(claim))

    # No verifiable numbers in claim = nothing to check
    if not claim_years and not claim_pcts:
        return 0.0

    source_years = set(_YEAR_RE.findall(source_text))
    source_pcts = set(_PERCENT_RE.findall(source_text))

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
            where={"memory_type": {"$in": _MEMORY_TYPES}},
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
# External (Cross-Model) Verification — Direct Structured Verdict
# ---------------------------------------------------------------------------


def _model_family(model_id: str) -> str:
    """Extract provider family from an OpenRouter model ID.

    ``"openrouter/openai/gpt-4o-mini"`` → ``"openai"``
    ``"openrouter/meta-llama/llama-3.3-70b"`` → ``"meta-llama"``
    """
    parts = model_id.lower().split("/")
    return parts[1] if len(parts) >= 3 else parts[0]


def _pick_verification_model(generating_model: str | None) -> str:
    """Select a non-rate-limited verification model from a different family.

    Strategy: model diversity prevents the verifier from reproducing
    the same hallucination as the generator.  All candidates are from
    ``VERIFICATION_MODEL_POOL`` (non-rate-limited models only).
    """
    gen_family = _model_family(generating_model) if generating_model else ""

    for candidate in config.VERIFICATION_MODEL_POOL:
        if _model_family(candidate) != gen_family:
            return candidate

    # No diverse model available — use primary verification model anyway
    return config.VERIFICATION_MODEL


def _is_current_event_claim(claim: str) -> bool:
    """Detect whether a claim is about current events or recent information.

    Current-event claims benefit from web-search-enabled verification
    (e.g., Grok with live web search) rather than static model knowledge.
    Returns True if 2+ current-event patterns match, or if a single strong
    temporal marker (recent year, "this year", etc.) is present.
    """
    matches = sum(1 for p in _CURRENT_EVENT_PATTERNS if p.search(claim))

    # A single strong temporal marker is sufficient
    if matches >= 1:
        # Check for strong temporal signals that alone warrant web search
        strong_temporal = [
            re.compile(r"\b(?:202[5-9]|203\d)\b"),              # year 2025+
            re.compile(r"\b(?:this year|last month|this month|last week|this week)\b", re.I),
            re.compile(r"\b(?:recently|just|newly)\b.*\b(?:announced|launched|released)\b", re.I),
        ]
        if any(p.search(claim) for p in strong_temporal):
            return True

    # Two or more weaker signals together also qualify
    return matches >= 2


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

        if verdict == "supported" and confidence >= 0.6:
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


async def _llm_call_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
) -> httpx.Response:
    """POST to an LLM endpoint with exponential backoff on 429 responses."""
    max_attempts = config.EXTERNAL_VERIFY_RETRY_ATTEMPTS
    base_delay = config.EXTERNAL_VERIFY_RETRY_BASE_DELAY

    for attempt in range(max_attempts):
        resp = await client.post(url, json=payload)
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


async def _verify_claim_externally(
    claim: str,
    generating_model: str | None = None,
    force_web_search: bool = False,
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

    # Detect ignorance-admitting claims ("I don't have info about X")
    # These always use web search and get inverted verdicts.
    is_ignorance = _is_ignorance_admission(claim)

    # Determine if the claim needs web-search verification.
    # Ignorance claims always go to web search — the model admitted not
    # knowing, so we need live search to check if the facts exist.
    is_current_event = force_web_search or is_ignorance or _is_current_event_claim(claim)

    if is_current_event:
        verify_model = config.VERIFICATION_CURRENT_EVENT_MODEL
        if is_ignorance:
            system_prompt = _SYSTEM_IGNORANCE_VERIFICATION
        else:
            system_prompt = _SYSTEM_CURRENT_EVENT_VERIFICATION
        verification_method = "web_search"
    else:
        verify_model = _pick_verification_model(generating_model)
        system_prompt = _SYSTEM_DIRECT_VERIFICATION
        verification_method = "cross_model"

    sem = _get_ext_verify_semaphore()

    async with sem:
        try:
            # Include generating model context so the verifier knows it's
            # checking another AI's output (prevents self-confirmation bias)
            model_context = (
                f"\n\nThis claim was generated by {generating_model}."
                if generating_model else ""
            )

            if is_ignorance:
                # Reframed prompt: check underlying facts, not the model's honesty
                user_prompt = (
                    f"An AI model said: \"{claim}\"\n\n"
                    f"The model is admitting it lacks knowledge about a topic. "
                    f"Do NOT evaluate whether the model is honest about its "
                    f"limitations. Instead, search for and verify whether the "
                    f"underlying facts, events, or information actually exist."
                    f"{model_context}\n\n"
                    f"Respond with ONLY a JSON object: "
                    f"{{\"verdict\": \"supported\"|\"refuted\"|\"insufficient_info\", "
                    f"\"confidence\": 0.0-1.0, \"reasoning\": \"...\"}}"
                )
            else:
                user_prompt = (
                    f"Assess this claim for factual accuracy:\n\n"
                    f"\"{claim}\"{model_context}\n\n"
                    f"Respond with ONLY a JSON object: "
                    f"{{\"verdict\": \"supported\"|\"refuted\"|\"insufficient_info\", "
                    f"\"confidence\": 0.0-1.0, \"reasoning\": \"...\"}}"
                )

            url = f"{config.BIFROST_URL}/chat/completions"
            payload = {
                "model": verify_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": config.EXTERNAL_VERIFY_TEMPERATURE,
                "max_tokens": config.EXTERNAL_VERIFY_MAX_TOKENS,
            }

            # Increase timeout for web-search calls — they take longer
            timeout = config.BIFROST_TIMEOUT * 2 if is_current_event else config.BIFROST_TIMEOUT

            breaker = get_breaker("bifrost-verify")

            async def _bifrost_verify() -> dict:
                async with httpx.AsyncClient(timeout=timeout, headers=tracing_headers()) as client:
                    resp = await _llm_call_with_retry(client, url, payload)
                    return resp.json()

            data = await breaker.call(_bifrost_verify)
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
            # For claims like "I don't have info about X", the verifier checks
            # whether X exists.  If X exists (verdict = "verified"), the model's
            # response was inadequate → invert to "unverified".
            if is_ignorance:
                verdict = _invert_ignorance_verdict(verdict)
                logger.info(
                    "Ignorance-admission claim detected, verdict inverted: "
                    "'%s...' → %s",
                    claim[:50],
                    verdict["status"],
                )

            # --- Staleness escalation ---
            # If a static model says "supported" or "uncertain" but its
            # reasoning admits stale knowledge, escalate to web search for
            # a second opinion.  Only triggers when:
            #  1. NOT already a forced web search (prevents infinite recursion)
            #  2. NOT an ignorance claim (already handled above)
            #  3. The claim looks like it could be about current events
            #  4. The reasoning contains staleness indicators
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
                    claim, generating_model, force_web_search=True
                )

            return {
                **verdict,
                "verification_method": verification_method,
                "verification_model": verify_model,
                "verification_answer": raw_answer,
                "source_urls": source_urls,
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
# Claim verification
# ---------------------------------------------------------------------------

async def verify_claim(
    claim: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: float | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Verify a single claim against the knowledge base and user memories.

    Uses multi-result triangulation, numeric contradiction detection,
    and memory authority to produce calibrated confidence scores.

    Falls back to cross-model external verification when the KB has no
    relevant content (prevents false negatives on valid factual claims).
    """
    from agents.query_agent import agent_query

    if threshold is None:
        threshold = config.HALLUCINATION_THRESHOLD
    unverified_threshold = config.HALLUCINATION_UNVERIFIED_THRESHOLD
    ext_kb_threshold = config.EXTERNAL_VERIFY_KB_THRESHOLD

    try:
        # Exclude 'conversations' domain from general KB query to avoid
        # self-verification against feedback-ingested LLM responses.
        verification_domains = [d for d in config.DOMAINS if d != "conversations"]
        result = await agent_query(
            query=claim,
            domains=verification_domains,
            top_k=5,
            use_reranking=False,  # speed over accuracy for verification
            chroma_client=chroma_client,
            redis_client=redis_client,
            neo4j_driver=neo4j_driver,
        )

        # Also query user-confirmed memories (filtered by memory_type)
        memory_results = await _query_memories(claim, chroma_client, top_k=2)

        # Merge KB results with memory results
        all_results = list(result.get("results", []))
        for mr in memory_results:
            # Memories get an authority boost (user-confirmed content)
            mr["relevance"] = min(1.0, round(mr["relevance"] + _MEMORY_AUTHORITY_BOOST, 4))
            all_results.append(mr)

        # Sort by relevance descending
        all_results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)

        # --- Fallback 1: No KB results at all → try external verification ---
        if not all_results:
            ext_result = await _verify_claim_externally(claim, model)
            return {
                "claim": claim,
                "status": ext_result["status"],
                "similarity": ext_result["confidence"],
                "reason": ext_result["reason"],
                "verification_method": ext_result.get("verification_method", "none"),
                "verification_model": ext_result.get("verification_model"),
                "source_urls": ext_result.get("source_urls", []),
            }

        top_results = all_results[:3]
        top_result = top_results[0]
        raw_similarity = top_result.get("relevance", 0.0)

        # --- Fallback 2: Very low KB similarity → try external verification ---
        if raw_similarity < ext_kb_threshold:
            ext_result = await _verify_claim_externally(claim, model)
            # Use external result if it provides a stronger signal than KB
            if ext_result["confidence"] > raw_similarity:
                return {
                    "claim": claim,
                    "status": ext_result["status"],
                    "similarity": ext_result["confidence"],
                    "reason": ext_result["reason"],
                    "verification_method": ext_result.get("verification_method", "none"),
                    "verification_model": ext_result.get("verification_model"),
                    "source_urls": ext_result.get("source_urls", []),
                }

        # Apply multi-result confidence calibration
        similarity = _compute_adjusted_confidence(claim, top_results, raw_similarity)
        details = _build_verification_details(claim, top_results)

        if similarity >= threshold:
            return {
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
            }
        elif similarity < unverified_threshold:
            return {
                "claim": claim,
                "status": "unverified",
                "similarity": round(similarity, 3),
                "reason": details.get("reason", "Low similarity to any KB content"),
                "verification_details": details,
                "verification_method": "kb",
            }
        else:
            return {
                "claim": claim,
                "status": "uncertain",
                "similarity": round(similarity, 3),
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "reason": details.get("reason", "Partial match — review recommended"),
                "verification_details": details,
                "verification_method": "kb",
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
# Main orchestration
# ---------------------------------------------------------------------------

async def check_hallucinations(
    response_text: str,
    conversation_id: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: float | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Extract claims, verify each against KB, and store results in Redis."""
    if threshold is None:
        threshold = config.HALLUCINATION_THRESHOLD
    min_length = config.HALLUCINATION_MIN_RESPONSE_LENGTH

    if len(response_text) < min_length:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "skipped": True,
            "reason": f"Response too short ({len(response_text)} chars < {min_length})",
            "claims": [],
            "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        }

    claims, method = await extract_claims(response_text)
    if not claims:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "skipped": True,
            "reason": "No factual claims extracted",
            "extraction_method": method,
            "claims": [],
            "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        }

    results = await asyncio.gather(*[
        verify_claim(claim, chroma_client, neo4j_driver, redis_client, threshold, model=model)
        for claim in claims
    ])

    status_counts = {"verified": 0, "unverified": 0, "uncertain": 0, "error": 0}
    for r in results:
        status = r.get("status", "error")
        if status in status_counts:
            status_counts[status] += 1

    report = {
        "conversation_id": conversation_id,
        "timestamp": utcnow_iso(),
        "skipped": False,
        "threshold": threshold,
        "model": model,
        "extraction_method": method,
        "claims": list(results),
        "summary": {
            "total": len(results),
            **status_counts,
        },
    }

    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(report))
    except Exception as e:
        logger.warning("Failed to store hallucination report in Redis: %s", e)

    # Log verification metrics for analytics
    try:
        from utils.cache import log_verification_metrics
        log_verification_metrics(
            redis_client,
            conversation_id=conversation_id,
            model=model,
            verified=status_counts["verified"],
            unverified=status_counts["unverified"],
            uncertain=status_counts["uncertain"],
            total=len(results),
        )
    except Exception as e:
        logger.debug("Failed to log verification metrics (non-blocking): %s", e)

    return report


async def verify_response_streaming(
    response_text: str,
    conversation_id: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: float | None = None,
    model: str | None = None,
):
    """Streaming verification generator — yields claim results as they are verified.

    Results are yielded as they complete (parallel execution), then persisted
    to Redis after the final summary for audit analytics and conversation revisits.
    """
    if threshold is None:
        threshold = config.HALLUCINATION_THRESHOLD
    min_length = config.HALLUCINATION_MIN_RESPONSE_LENGTH

    if len(response_text) < min_length:
        yield {
            "type": "summary",
            "overall_confidence": 0,
            "verified": 0,
            "unverified": 0,
            "uncertain": 0,
            "total": 0,
            "skipped": True,
            "reason": f"Response too short ({len(response_text)} chars)",
        }
        return

    claims, method = await extract_claims(response_text)
    if not claims:
        yield {
            "type": "summary",
            "overall_confidence": 0,
            "verified": 0,
            "unverified": 0,
            "uncertain": 0,
            "total": 0,
            "skipped": True,
            "reason": "No factual claims extracted",
            "extraction_method": method,
        }
        return

    # Notify frontend of extraction method and all extracted claims
    yield {"type": "extraction_complete", "method": method, "count": len(claims)}

    for i, claim in enumerate(claims):
        yield {"type": "claim_extracted", "claim": claim, "index": i}

    # --- Parallel verification via asyncio.as_completed ---
    verified_count = 0
    unverified_count = 0
    uncertain_count = 0
    assessed_confidence = 0.0  # Only accumulate for verified/unverified
    assessed_count = 0
    collected_results: list[dict[str, Any] | None] = [None] * len(claims)

    async def _verify_indexed(idx: int, claim_text: str) -> tuple[int, dict[str, Any]]:
        result = await verify_claim(
            claim_text, chroma_client, neo4j_driver, redis_client, threshold, model=model
        )
        return idx, result

    tasks = [_verify_indexed(i, claim) for i, claim in enumerate(claims)]

    for coro in asyncio.as_completed(tasks):
        i, result = await coro
        status = result.get("status", "error")
        confidence = result.get("similarity", 0.0)

        if status == "verified":
            verified_count += 1
            assessed_confidence += confidence
            assessed_count += 1
        elif status == "unverified":
            unverified_count += 1
            assessed_confidence += confidence
            assessed_count += 1
        else:
            # Uncertain/unassessable claims excluded from confidence avg
            uncertain_count += 1

        collected_results[i] = result

        yield {
            "type": "claim_verified",
            "index": i,
            "claim": claims[i],
            "status": status,
            "confidence": confidence,
            "source": result.get("source_filename", ""),
            "source_artifact_id": result.get("source_artifact_id", ""),
            "source_domain": result.get("source_domain", ""),
            "source_snippet": result.get("source_snippet", ""),
            "reason": result.get("reason", ""),
            "verification_method": result.get("verification_method", "kb"),
            "verification_model": result.get("verification_model"),
            "source_urls": result.get("source_urls", []),
        }

    # Confidence averaged over assessed claims only (verified + unverified).
    # Uncertain/unassessable claims are neutral and excluded from the average.
    overall = (assessed_confidence / assessed_count) if assessed_count > 0 else 0
    yield {
        "type": "summary",
        "overall_confidence": round(overall, 3),
        "verified": verified_count,
        "unverified": unverified_count,
        "uncertain": uncertain_count,
        "total": len(claims),
        "assessed": assessed_count,
        "extraction_method": method,
    }

    # --- Persist to Redis (same format as batch path) ---
    status_counts = {
        "verified": verified_count,
        "unverified": unverified_count,
        "uncertain": uncertain_count,
    }
    report = {
        "conversation_id": conversation_id,
        "timestamp": utcnow_iso(),
        "skipped": False,
        "threshold": threshold,
        "model": model,
        "extraction_method": method,
        "claims": [r for r in collected_results if r is not None],
        "summary": {
            "total": len(claims),
            **status_counts,
        },
    }
    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(report))
    except Exception as e:
        logger.warning("Failed to persist streaming report to Redis: %s", e)

    try:
        from utils.cache import log_verification_metrics
        log_verification_metrics(
            redis_client,
            conversation_id=conversation_id,
            model=model,
            verified=verified_count,
            unverified=unverified_count,
            uncertain=uncertain_count,
            total=len(claims),
        )
    except Exception as e:
        logger.debug("Failed to log streaming verification metrics: %s", e)


def get_hallucination_report(
    redis_client,
    conversation_id: str,
) -> dict[str, Any] | None:
    """Retrieve a previously stored hallucination report."""
    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        data = redis_client.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning("Failed to retrieve hallucination report: %s", e)
    return None
