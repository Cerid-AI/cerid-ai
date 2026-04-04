# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination detection — shared patterns, constants, and concurrency gates.

This module contains all compiled regex patterns, constants, and semaphore
accessors used across the hallucination detection subsystem.
"""

from __future__ import annotations

import asyncio
import re

import config

# ---------------------------------------------------------------------------
# Constants — configurable via env vars in config/settings.py
# ---------------------------------------------------------------------------

# Memory types that count as user-confirmed facts for verification
MEMORY_TYPES = ["fact", "decision", "preference", "action_item"]

# Authority boost for memory-sourced results (user-confirmed content)
MEMORY_AUTHORITY_BOOST = 0.15

# ---------------------------------------------------------------------------
# Concurrency gates
# ---------------------------------------------------------------------------

# Concurrency gate for external verification — defense-in-depth against
# bursts even on high-RPM models.  Python 3.10+ asyncio primitives no
# longer require a running event loop at creation time.
_ext_verify_semaphore = asyncio.Semaphore(config.EXTERNAL_VERIFY_MAX_CONCURRENT)

# Concurrency gate for overall claim verification (KB search + reranking +
# external LLM).  Each verification loads BM25 indices and runs ONNX
# cross-encoder inference which is memory-intensive.  Without this,
# 10+ parallel claims can OOM a 2 GB container.
_claim_verify_semaphore = asyncio.Semaphore(config.VERIFY_CLAIM_MAX_CONCURRENT)


def _get_ext_verify_semaphore() -> asyncio.Semaphore:
    """Return the external-verification semaphore."""
    return _ext_verify_semaphore


def _get_claim_verify_semaphore() -> asyncio.Semaphore:
    """Return the claim-verification concurrency semaphore."""
    return _claim_verify_semaphore


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

# Sentence-ending pattern for heuristic extraction
SENTENCE_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z])"
)

# ---------------------------------------------------------------------------
# Factual claim patterns
# ---------------------------------------------------------------------------

# Patterns that indicate a sentence contains a verifiable factual claim
FACTUAL_PATTERNS = [
    re.compile(r"\b\d{4}\b"),                         # years
    re.compile(r"\b\d+(?:\.\d+)?%"),                   # percentages
    re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b"),      # numbers
    re.compile(r"\b(?:is|are|was|were|has|have|had)\b", re.I),  # state verbs
    re.compile(r"\b(?:released|created|founded|introduced|built|developed)\b", re.I),
    re.compile(r"\b(?:supports?|supporting|requires?|includes?|including|provides?|providing|uses?|using|contains?|containing|manages?|managing)\b", re.I),
    # Entity claims (proper nouns followed by verbs) — up to 4 proper nouns
    re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+){0,3}\s(?:is|are|was|were|use|uses|run|runs|has|have)\b"),
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
NON_FACTUAL_PATTERNS = [
    re.compile(r"^\s*(?:I |you |let me|sure|okay|here|note|please|thanks|hello|hi )", re.I),
    re.compile(r"^\s*(?:would you|can I|shall I|do you want)", re.I),
    re.compile(r"^\s*```"),                             # code blocks
    re.compile(r"^\s*(?:for example|e\.g\.|i\.e\.)\s*$", re.I),   # standalone example markers
    re.compile(r"^\s*\d+\.\s*$"),                                  # bare numbered list markers
    re.compile(r"^\s*(?:import |from \w+ import |def |class |const |let |var |function )", re.I),  # code
    re.compile(r"(?:feel free|don'?t hesitate|happy to (?:help|assist)|hope (?:this|that) helps|let me know|if you (?:have|need) (?:any|more) (?:questions|help))", re.I),  # pleasantries
    re.compile(r"^\s*(?:in (?:summary|conclusion|short)|to (?:summarize|sum up|recap))\s*[,:]?\s*$", re.I),  # standalone summary markers
    re.compile(r"^\s*(?:overall|essentially|basically)\s*[,:]", re.I),  # filler openers
]

# Strong-signal patterns — a single match counts as 2 for scoring purposes
STRONG_FACTUAL_PATTERNS = [
    re.compile(r"\b(?:faster|slower|better|worse|larger|smaller|more|fewer)\s+than\b", re.I),
    re.compile(r"\b(?:according to|reported by|published by)\b", re.I),
    re.compile(r"\b\d{4}\b.*\b(?:released|created|founded|introduced|launched)\b", re.I),
    # "X is a/an Y" definitional claims (e.g., "Python is a programming language")
    re.compile(r"\b\w+\s+is\s+a(?:n)?\s+\w+", re.I),
    # "was created/founded/developed by" attribution claims
    re.compile(r"\b(?:created|founded|developed|invented)\s+by\s+[A-Z]"),
    # "was born/established/released in" temporal claims
    re.compile(r"\b(?:was|were)\s+(?:born|established|released|published|introduced)\s+in\b", re.I),
]

# ---------------------------------------------------------------------------
# Current event / staleness patterns
# ---------------------------------------------------------------------------

# Patterns that indicate a claim is about current events or recent information.
# These claims benefit from web-search-enabled verification (Grok + web search).
CURRENT_EVENT_PATTERNS = [
    re.compile(r"\b(?:202[4-9]|203\d)\b"),                      # recent/future years
    re.compile(r"\b(?:recently|just|newly|latest|current|now)\b", re.I),
    re.compile(r"(?:\b(?:announced|launched|released|unveiled|introduced|updated)\b.*\b(?:202[4-9]|this year|last (?:month|week|year))\b|\b(?:202[4-9]|this year|last (?:month|week|year))\b.*\b(?:announced|launched|released|unveiled|introduced|updated)\b)", re.I),
    re.compile(r"\b(?:this year|last year|this month|last month|this week|last week)\b", re.I),
    re.compile(r"\b(?:as of|since|starting|beginning|effective)\s+(?:202[4-9]|January|February|March|April|May|June|July|August|September|October|November|December)\b", re.I),
    re.compile(r"\b(?:trending|breaking|emerging|ongoing)\b", re.I),
    re.compile(r"\b(?:CEO|president|prime minister|acquired|merger|IPO|bankruptcy|election)\b", re.I),
    re.compile(r"\b(?:version|v)\s*\d+\.\d+.*\b(?:released|launched|available|shipped)\b", re.I),
    # Prices, costs, fees — inherently time-sensitive and change frequently
    re.compile(r"(?:€|\$|£|¥|₹)\s*\d+[\d.,]*\b", re.I),       # currency symbols + amounts
    re.compile(r"\b(?:costs?|prices?|fees?|fares?|rates?|charges?|subscription|salary|salaries|wages?)\b.*\b(?:\d+[\d.,]*)\b", re.I),
    re.compile(r"\b(?:ticket|admission|entry|membership)\b.*\b(?:€|\$|£|¥|₹|\d+[\d.,]*)\b", re.I),
]

# Patterns that indicate a verification model's response admits stale knowledge.
STALE_KNOWLEDGE_PATTERNS = [
    re.compile(r"as of (?:my|the) (?:last |latest )?(?:training|knowledge|data|update)", re.I),
    re.compile(r"(?:I |my )(?:training|knowledge) (?:cutoff|cut-off|only goes|ends|stops)", re.I),
    re.compile(r"I (?:don'?t|do not) have (?:information|data|knowledge) (?:after|beyond|past)", re.I),
    re.compile(r"(?:may have|might have|could have) changed since", re.I),
    re.compile(r"(?:unable to|cannot) (?:verify|confirm|access) (?:current|recent|latest)", re.I),
    re.compile(r"(?:not aware of|no information about) (?:any )?(?:recent|latest|current)", re.I),
]

# ---------------------------------------------------------------------------
# Ignorance admission patterns
# ---------------------------------------------------------------------------

# Patterns that detect when a claim is an *admission of ignorance* by the
# generating model rather than a positive factual assertion.
IGNORANCE_ADMISSION_PATTERNS = [
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
    # Capability limitations
    re.compile(r"I'?m unable to\b", re.I),
    re.compile(r"I cannot access\b", re.I),
    re.compile(r"I don'?t have the ability\b", re.I),
    re.compile(r"beyond my capabilities\b", re.I),
    # Real-time data caveats (require negation/limitation context to avoid matching recency hedges)
    re.compile(r"I don'?t have real-time\b", re.I),
    re.compile(r"I cannot browse\b", re.I),
    re.compile(r"my training data (?:does not|doesn'?t|may not|cannot|only)", re.I),
    re.compile(r"my knowledge cutoff (?:prevents|limits|means I can'?t|does not)", re.I),
    re.compile(r"I lack access to\b", re.I),
]

# ---------------------------------------------------------------------------
# Evasion patterns
# ---------------------------------------------------------------------------

# Patterns that indicate the model is deflecting/hedging instead of answering
EVASION_PATTERNS = [
    re.compile(
        r"(?:it'?s|this is) (?:important|crucial|worth|essential) to "
        r"(?:note|consider|remember|understand|recognize|acknowledge)",
        re.I,
    ),
    re.compile(
        r"(?:I |it'?s )(?:not (?:appropriate|possible|accurate|helpful)|"
        r"difficult|challenging) to (?:provide|give|make|single out|pinpoint|"
        r"attribute|assign)",
        re.I,
    ),
    re.compile(
        r"(?:many|various|multiple|several|numerous|a (?:wide |complex )?range of) "
        r"factors (?:contribute|play|are involved|influence|affect)",
        re.I,
    ),
    re.compile(
        r"this is a (?:complex|nuanced|multifaceted|sensitive|"
        r"deeply complex|highly nuanced) (?:topic|issue|question|area|subject)",
        re.I,
    ),
    re.compile(
        r"(?:I |we )(?:should|must|need to) "
        r"(?:be careful|avoid|refrain from|be cautious about)",
        re.I,
    ),
    re.compile(
        r"(?:would be|it'?s) (?:irresponsible|misleading|oversimplistic|"
        r"reductive|inaccurate|unfair) to",
        re.I,
    ),
    re.compile(
        r"(?:rather than|instead of) (?:singling out|focusing on|pointing to|"
        r"isolating|attributing.*to) (?:specific|particular|any one|a single)",
        re.I,
    ),
    re.compile(
        r"there (?:is no|isn'?t a?) (?:simple|straightforward|easy|single|"
        r"one-size-fits-all) answer",
        re.I,
    ),
    re.compile(
        r"(?:correlation|association) (?:does not|doesn'?t) (?:imply|mean|equal) "
        r"causation",
        re.I,
    ),
]

# Patterns that indicate the user asked a specific quantitative/factual question
SPECIFIC_QUESTION_PATTERNS = [
    re.compile(r"\b(?:how many|how much|what percentage|what proportion|what fraction)\b", re.I),
    re.compile(r"\b(?:which (?:group|demographic|category|country|state|city|race|ethnicity))\b", re.I),
    re.compile(r"\b(?:who (?:has|leads|commits|is|are|does|did|was|were))\b", re.I),
    re.compile(r"\b(?:what (?:is|are|was|were) the (?:rate|number|count|total|amount|figure|ratio))\b", re.I),
    re.compile(r"\b(?:rank|top|most|least|highest|lowest|leading|largest|smallest)\b", re.I),
    re.compile(r"\b(?:per capita|per 100k?|per thousand|per million)\b", re.I),
    re.compile(r"\b(?:breakdown|distribution|statistics|stats|data|figures)\b", re.I),
]

# Pattern for detecting concrete data in a response (numbers, percentages, etc.)
CONCRETE_DATA_RE = re.compile(r"\b\d+(?:[.,]\d+)?(?:\s*%|\s+(?:per|out of|in every))\b")

# ---------------------------------------------------------------------------
# Citation patterns
# ---------------------------------------------------------------------------

CITATION_PATTERNS = [
    re.compile(
        r"(?:according to|per|as reported by|as stated (?:in|by)|"
        r"cited (?:in|by)|published (?:in|by)) (?:a |the )?"
        r"([A-Z][^,.\n]{3,80})",
        re.I,
    ),
    # Academic style: (Author, Year) or (Author et al., Year)
    re.compile(r"\(([A-Z][A-Za-z\s&]+(?:et al\.)?,?\s*\d{4})\)"),
    # "study/paper/report by/from/in ..."
    re.compile(
        r"(?:study|paper|report|article|survey|analysis) "
        r"(?:by|from|in) (?:the )?([A-Z][^,.\n]{3,80})",
        re.I,
    ),
]

# Well-known sources that don't need citation verification
KNOWN_SOURCES = {
    "cdc", "bls", "fbi", "census bureau", "who", "epa", "doj", "nih",
    "wikipedia", "britannica", "reuters", "ap", "associated press",
    "pew research", "gallup", "world bank", "imf", "oecd", "un",
    "google", "microsoft", "apple", "amazon", "meta",
}

# ---------------------------------------------------------------------------
# Complex claim patterns
# ---------------------------------------------------------------------------

# Regex patterns that indicate a claim requires stronger reasoning —
# causal chains, comparative judgments, multi-hop logic, quantitative
# comparisons — where a lightweight model (GPT-4o-mini) may give
# shallow or incorrect verdicts.
COMPLEX_CLAIM_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(?:because|due to|caused by|leads? to|results? in|as a result)\b",
        r"\b(?:faster|slower|better|worse|higher|lower|more|less|larger|smaller) than\b",
        r"\b(?:therefore|consequently|hence|thus|implies that)\b",
        r"\b(?:if .+ then|assuming|given that|provided that)\b",
        r"\b(?:unlike|whereas|in contrast|compared to|relative to)\b",
        r"\b(?:requires|depends on|prerequisite|necessary for)\b",
        r"\b(?:increased|decreased|doubled|tripled|grew|declined) .{0,20} (?:by|from|to) \d",
        # Arithmetic / computation
        r"\b\d+\s*[×÷+\-*/]\s*\d+",
        r"\b(?:factorial|probability|sum of|product of|average of|total of)\b",
        # Logical reasoning (quantifier + conclusion)
        r"\b(?:every|all|no|none|some)\b.*\b(?:therefore|thus|hence|must|cannot|implies)\b",
        # Statistical claims
        r"\b(?:average|median|mean|standard deviation|correlation|statistically significant|p-value|confidence interval)\b",
    ]
]

# ---------------------------------------------------------------------------
# Numeric extraction patterns for contradiction detection
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
PERCENT_RE = re.compile(r"\b(\d+(?:\.\d+)?%)")


# ---------------------------------------------------------------------------
# Helper functions operating on patterns
# ---------------------------------------------------------------------------

def _has_staleness_indicators(text: str) -> bool:
    """Detect if a verification response admits knowledge staleness."""
    return any(p.search(text) for p in STALE_KNOWLEDGE_PATTERNS)


def _is_recency_claim(claim: str) -> bool:
    """Detect if a claim explicitly references potentially outdated training data.

    Distinguishes from pure ignorance: recency claims contain actual facts that
    may be stale (e.g., "As of my last update, the population is 330M"), whereas
    ignorance claims say "I don't have information about X".
    """
    return any(p.search(claim) for p in STALE_KNOWLEDGE_PATTERNS)


def _is_ignorance_admission(claim: str) -> bool:
    """Detect whether a claim is an admission of ignorance by the generating model.

    Returns True if the claim contains language indicating the model doesn't
    have information about a topic, rather than making a positive factual
    assertion.  Such claims need special verification: instead of checking
    whether the model is being honest, we check whether the underlying facts
    actually exist.
    """
    return any(p.search(claim) for p in IGNORANCE_ADMISSION_PATTERNS)


def _is_complex_claim(claim: str) -> bool:
    """Detect claims that require stronger reasoning to verify.

    Complex claims involve causal chains, comparative judgments, quantitative
    comparisons, or conditional logic.  These benefit from a more capable
    model (Gemini 2.5 Flash) rather than GPT-4o-mini.
    """
    return sum(1 for p in COMPLEX_CLAIM_PATTERNS if p.search(claim)) >= 1


def _is_current_event_claim(claim: str) -> bool:
    """Detect whether a claim is about current events or recent information.

    Current-event claims benefit from web-search-enabled verification
    (e.g., Grok with live web search) rather than static model knowledge.
    Returns True if 2+ current-event patterns match, or if a single strong
    temporal marker (recent year, "this year", etc.) is present.
    """
    matches = sum(1 for p in CURRENT_EVENT_PATTERNS if p.search(claim))

    # A single strong temporal marker is sufficient
    if matches >= 1:
        # Check for strong temporal signals that alone warrant web search
        strong_temporal = [
            re.compile(r"\b(?:202[4-9]|203\d)\b"),              # year 2024+
            re.compile(r"\b(?:this year|last month|this month|last week|this week)\b", re.I),
            re.compile(r"\b(?:recently|just|newly)\b.*\b(?:announced|launched|released)\b", re.I),
        ]
        if any(p.search(claim) for p in strong_temporal):
            return True

    # Two or more weaker signals together also qualify
    return matches >= 2


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
