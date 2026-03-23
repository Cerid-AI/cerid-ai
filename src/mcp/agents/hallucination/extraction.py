# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination detection — claim extraction (LLM + heuristic + special types).

Extracts verifiable factual claims from LLM responses using:
- LLM-based extraction (primary)
- Regex heuristic extraction (fallback)
- Ignorance-admission pre-extraction
- Evasion detection
- Citation fabrication detection
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import httpx

import config
from agents.hallucination.patterns import (
    CITATION_PATTERNS,
    CONCRETE_DATA_RE,
    EVASION_PATTERNS,
    FACTUAL_PATTERNS,
    KNOWN_SOURCES,
    NON_FACTUAL_PATTERNS,
    SENTENCE_RE,
    SPECIFIC_QUESTION_PATTERNS,
    STALE_KNOWLEDGE_PATTERNS,
    STRONG_FACTUAL_PATTERNS,
    _is_ignorance_admission,
)
from utils.circuit_breaker import CircuitOpenError
from utils.internal_llm import call_internal_llm
from utils.llm_client import call_llm
from utils.llm_parsing import parse_llm_json

logger = logging.getLogger("ai-companion.hallucination")

CURRENT_YEAR = datetime.now().year


def _reclassify_recency(claim_text: str, claim_type: str) -> str:
    """Reclassify factual claims as recency if they contain temporal references.

    Catches date-based claims about past/future events (e.g. "2024 elections
    are upcoming") that the stale-knowledge pattern detector would miss because
    they don't reference training data cutoffs.
    """
    if claim_type != "factual":
        return claim_type  # Don't override non-factual types

    text_lower = claim_text.lower()

    # Check for year references that are stale (before current year)
    year_pattern = re.findall(r"\b(20[1-9]\d)\b", claim_text)
    for y in year_pattern:
        if int(y) < CURRENT_YEAR:
            # Claim references a past year with present/future tense
            if any(
                w in text_lower
                for w in (
                    "upcoming", "will ", "going to", "is expected",
                    "are expected", "plans to", "set to",
                )
            ):
                return "recency"

    # Check for explicit temporal markers that suggest time-sensitive info
    recency_markers = [
        r"\b(current|currently|now|today|this year|this month)\b",
        r"\b(recent|recently|latest|newest|just)\b",
        r"\b(upcoming|forthcoming|soon|next)\b",
        r"\b(as of \d{4}|since \d{4})\b",
    ]
    for pattern in recency_markers:
        if re.search(pattern, text_lower):
            return "recency"

    return claim_type


# ---------------------------------------------------------------------------
# System prompts for LLM-based extraction
# ---------------------------------------------------------------------------

_SYSTEM_CLAIM_EXTRACTION = (
    "You are a factual claim extraction engine for a verification system. "
    "Your job is to identify every verifiable factual statement in the text. "
    "A verifiable claim is any statement that could be checked against "
    "external sources — including dates, numbers, statistics, named entities, "
    "causal relationships, comparisons, attributions, and technical specifications. "
    "Do NOT extract opinions, greetings, questions, code examples, or conversational pleasantries "
    "(e.g., 'feel free to ask', 'hope this helps', 'let me know if you need more', 'happy to assist'). "
    "DO extract statements about knowledge cutoffs, data availability, and "
    "admissions of lacking information — these are verifiable claims about "
    "the model's capabilities and the existence of underlying data. "
    "Return ONLY a JSON array of objects, each with:\n"
    '  {"claim": "<the factual statement>", "type": "<category>"}\n'
    "Valid types: date, statistic, attribution, comparison, technical, "
    "definition, causal, general.\n"
    "If the text contains no verifiable claims, return []."
)


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

    sentences = SENTENCE_RE.split(text)
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
        if any(p.search(sentence) for p in NON_FACTUAL_PATTERNS):
            continue

        # Check for factual indicators
        factual_score = sum(1 for p in FACTUAL_PATTERNS if p.search(sentence))

        # Strong-signal patterns provide a bonus (single strong match + 1 base = qualifies)
        strong_bonus = sum(1 for p in STRONG_FACTUAL_PATTERNS if p.search(sentence))
        effective_score = factual_score + strong_bonus

        if effective_score >= 1:
            # Clean up trailing punctuation for consistency
            claim = sentence.rstrip(".,;: ")
            if claim and claim not in claims:
                claims.append(claim)
                if len(claims) >= max_claims:
                    break

    return claims


# ---------------------------------------------------------------------------
# Ignorance claim pre-extraction
# ---------------------------------------------------------------------------

def _extract_ignorance_claims(response_text: str) -> list[str]:
    """Pre-extraction pass: surface ignorance admissions and recency limitations.

    The LLM extraction prompt excludes "meta-commentary" and the heuristic
    ``NON_FACTUAL_PATTERNS`` blocks sentences starting with "I ".  This
    means legitimate ignorance admissions ("I don't have access to data from
    2025") and knowledge-cutoff statements ("As of my last update in October
    2023") are silently dropped before ``IGNORANCE_ADMISSION_PATTERNS`` in
    ``_verify_claim_externally`` can ever see them.

    This function runs *before* LLM/heuristic extraction and pulls these
    claims directly from the response so they reach downstream verification.
    """
    text = response_text[:5000]
    sentences = SENTENCE_RE.split(text)
    expanded: list[str] = []
    for s in sentences:
        expanded.extend(line.strip() for line in s.split("\n") if line.strip())

    claims: list[str] = []
    seen: set[str] = set()
    for sentence in expanded:
        if len(sentence) < 20 or len(sentence) > 300:
            continue
        if _is_ignorance_admission(sentence):
            clean = sentence.rstrip(".,;: ")
            if clean and clean not in seen:
                claims.append(clean)
                seen.add(clean)
        # Also catch knowledge-cutoff / staleness statements that aren't
        # phrased as ignorance admissions (e.g. "As of my last update...")
        elif any(p.search(sentence) for p in STALE_KNOWLEDGE_PATTERNS):
            clean = sentence.rstrip(".,;: ")
            if clean and clean not in seen:
                claims.append(clean)
                seen.add(clean)
    return claims


# ---------------------------------------------------------------------------
# Evasion detection — model hedges instead of answering specific questions
# ---------------------------------------------------------------------------

def _detect_evasion(response_text: str, user_query: str | None) -> list[str]:
    """Detect when a model evades answering a specific factual question.

    Returns synthesized claims for verification when evasion is detected.
    These claims reframe the user's original question so Grok can answer it.
    """
    if not user_query:
        return []

    # Check if user asked a specific/quantitative question
    is_specific_question = any(p.search(user_query) for p in SPECIFIC_QUESTION_PATTERNS)
    if not is_specific_question:
        return []

    # Count evasion signals in the response
    text = response_text[:5000]
    evasion_hits = sum(1 for p in EVASION_PATTERNS if p.search(text))

    if evasion_hits < 2:
        return []  # Need at least 2 hedging signals

    # Check if the response lacks concrete data despite a specific question
    has_concrete_data = bool(CONCRETE_DATA_RE.search(text))
    if has_concrete_data and evasion_hits < 4:
        return []  # Has some data + fewer hedges = not full evasion

    # Evasion confirmed — synthesize a verification claim from the user's query
    # Truncate query to a reasonable length for the claim
    query_text = user_query.strip()
    if len(query_text) > 200:
        query_text = query_text[:200].rsplit(" ", 1)[0] + "..."

    claim = (
        f"[EVASION] The user asked: \"{query_text}\" — "
        f"The model deflected with {evasion_hits} hedging patterns "
        f"instead of providing concrete data"
    )
    logger.info("Evasion detected (%d signals): %s", evasion_hits, query_text[:80])
    return [claim]


# ---------------------------------------------------------------------------
# Citation fabrication detection
# ---------------------------------------------------------------------------

def _extract_citation_claims(response_text: str) -> list[str]:
    """Extract citations from response text for fabrication checking.

    Returns synthetic [CITATION] claims for each unique cited source that
    isn't in the well-known sources list.
    """
    seen: set[str] = set()
    claims: list[str] = []

    for pattern in CITATION_PATTERNS:
        for match in pattern.finditer(response_text[:5000]):
            source = match.group(1).strip().rstrip(".")
            # Skip very short or very long matches
            if len(source) < 5 or len(source) > 100:
                continue
            source_lower = source.lower()
            # Skip well-known sources (word-boundary match to avoid
            # false positives like "un" matching inside "found")
            if any(
                re.search(r"\b" + re.escape(known) + r"\b", source_lower)
                for known in KNOWN_SOURCES
            ):
                continue
            # Deduplicate
            if source_lower in seen:
                continue
            seen.add(source_lower)
            claims.append(f"[CITATION] {source}")

    if claims:
        logger.info("Extracted %d citation claims for verification", len(claims))
    return claims


# ---------------------------------------------------------------------------
# LLM-based claim extraction
# ---------------------------------------------------------------------------

async def _extract_claims_llm(response_text: str, max_claims: int) -> list[str]:
    """Extract factual claims using the verification LLM model."""
    user_prompt = (
        f"Extract up to {max_claims} verifiable factual claims from the text below.\n"
        "Include: dates, statistics, comparisons (X is faster/better than Y), "
        "causal statements (because, due to, leads to), attributions "
        "(according to, created by), technical specs, and definitions.\n"
        "Exclude: opinions, greetings, questions, and code blocks.\n"
        "Include knowledge-cutoff admissions and data-availability claims "
        "(e.g., 'As of my last update...', 'I don't have access to...').\n"
        "For list items that contain facts, extract the factual part as a standalone claim.\n\n"
        "Examples:\n"
        'Input: "Paris is the capital of France. I hope this helps!"\n'
        'Output: [{"claim": "Paris is the capital of France", "type": "general"}]\n\n'
        'Input: "The temperature is 72\u00b0F. However, I should note that weather data changes frequently."\n'
        'Output: [{"claim": "The temperature is 72\u00b0F", "type": "statistic"}]\n\n'
        'Input: "I don\'t have access to that information, but generally speaking, '
        'water boils at 100\u00b0C at sea level."\n'
        'Output: [{"claim": "Water boils at 100\u00b0C at sea level", "type": "technical"}]\n\n'
        f"Text:\n{response_text[:5000]}\n\n"
        "JSON array:"
    )

    # Model fallback chain: try free models first, then paid
    fallback_models = [
        config.LLM_INTERNAL_MODEL,                            # Free tier (Llama 3.3 70B)
        config.VERIFICATION_MODEL,                             # Paid (GPT-4o-mini)
        "openrouter/google/gemini-2.5-flash-preview-05-20",   # Free fallback
    ]
    # Deduplicate while preserving order
    seen_models: set[str] = set()
    models: list[str] = []
    for m in fallback_models:
        if m and m not in seen_models:
            models.append(m)
            seen_models.add(m)

    messages = [
        {"role": "system", "content": _SYSTEM_CLAIM_EXTRACTION},
        {"role": "user", "content": user_prompt},
    ]

    # Try internal LLM first (Ollama if available, else routes to OpenRouter)
    try:
        content = await call_internal_llm(
            messages, temperature=0.1, max_tokens=1200,
            response_format={"type": "json_object"},
        )
        raw = parse_llm_json(content)
        if isinstance(raw, dict):
            raw = raw.get("claims", raw.get("results", raw.get("data", [])))
        if isinstance(raw, list):
            claims_internal: list[str] = []
            for item in raw[:max_claims]:
                if isinstance(item, dict):
                    claims_internal.append(str(item.get("claim", item.get("text", str(item)))))
                else:
                    claims_internal.append(str(item))
            if claims_internal:
                logger.info("Internal LLM claim extraction succeeded (%d claims)", len(claims_internal))
                return claims_internal
    except Exception as e:
        logger.debug("Internal LLM claim extraction failed (%s), trying external models", e)

    for model in models:
        try:
            content = await call_llm(
                messages,
                breaker_name="bifrost-claims",
                model=model,
                temperature=0.1,
                max_tokens=1200,
                response_format={"type": "json_object"},
            )
            raw = parse_llm_json(content)
            # response_format: json_object may wrap array in {"claims": [...]}
            if isinstance(raw, dict):
                raw = raw.get("claims", raw.get("results", raw.get("data", [])))
            if isinstance(raw, list):
                claims: list[str] = []
                for item in raw[:max_claims]:
                    if isinstance(item, dict):
                        claims.append(str(item.get("claim", item.get("text", str(item)))))
                    else:
                        claims.append(str(item))
                if claims:
                    logger.info("LLM claim extraction succeeded with model=%s (%d claims)", model, len(claims))
                    return claims
            logger.warning("LLM claim extraction returned unexpected shape from %s: %s", model, type(raw).__name__)
        except CircuitOpenError:
            logger.warning("Bifrost claims circuit open for %s, trying next model", model)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("Claim extraction timeout/connection error with %s: %s", model, e)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            logger.warning("Claim extraction HTTP %d from %s — trying next model", status, model)
            if status not in (402, 429, 503):
                break  # Non-retriable error, stop trying
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Claim extraction parse error from %s: %s", model, e)

    return []


# ---------------------------------------------------------------------------
# Main extraction orchestrator
# ---------------------------------------------------------------------------

async def extract_claims(
    response_text: str,
    user_query: str | None = None,
) -> tuple[list[str], str]:
    """Extract factual claims from an LLM response.

    Returns a tuple of (claims, method) where method is one of:
    - "llm": claims extracted via LLM
    - "heuristic": claims extracted via regex fallback
    - "ignorance": claims surfaced from ignorance-admission pre-extraction
    - "evasion": model evaded answering a specific factual question
    - "none": no claims could be extracted
    """
    min_length = config.HALLUCINATION_MIN_RESPONSE_LENGTH
    max_claims = config.HALLUCINATION_MAX_CLAIMS

    if len(response_text) < min_length:
        return [], "none"

    # Pre-extraction: surface ignorance admissions and recency limitations
    # that the LLM prompt ("exclude meta-commentary") and heuristic
    # (NON_FACTUAL_PATTERNS blocks "I ...") would otherwise drop.
    ignorance_claims = _extract_ignorance_claims(response_text)

    # Pre-extraction: detect evasion (model hedges instead of answering)
    evasion_claims = _detect_evasion(response_text, user_query) if user_query else []

    # Pre-extraction: detect cited sources for fabrication checking
    citation_claims = _extract_citation_claims(response_text)

    # Helper to merge special claims into a primary claim list
    def _merge_special(primary: list[str]) -> list[str]:
        merged = list(primary)
        merged_set = {c.lower() for c in primary}
        for extra in (ignorance_claims, evasion_claims, citation_claims):
            for c in extra:
                if c.lower() not in merged_set:
                    merged.append(c)
                    merged_set.add(c.lower())
        return merged[:max_claims]

    # Try LLM extraction first
    llm_claims = await _extract_claims_llm(response_text, max_claims)
    if llm_claims:
        return _merge_special(llm_claims), "llm"

    # Fallback to heuristic extraction
    logger.info("LLM claim extraction returned empty — falling back to heuristic")
    heuristic_claims = _extract_claims_heuristic(response_text)
    if heuristic_claims:
        return _merge_special(heuristic_claims), "heuristic"

    # Evasion claims alone are high priority — model refused to answer
    if evasion_claims:
        return _merge_special(evasion_claims), "evasion"

    # Last resort: ignorance or citation claims alone
    if ignorance_claims or citation_claims:
        combined = ignorance_claims + citation_claims
        return combined[:max_claims], "ignorance" if ignorance_claims else "citation"

    return [], "none"
