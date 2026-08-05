# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
#
# Metamorphic verification — Pro-tier hallucination scoring (Phase H).
#
# The plugin extracts atomic factual claims from a generated answer,
# uses an LLM to produce two semantic mutations of each claim:
#
#   - synonym: a rephrasing that should ALSO be entailed by the context
#   - antonym: a flipped statement that should NOT be entailed
#
# Then it tests each mutation against the source context with a
# heuristic entailment check (token-overlap + negation-aware). The
# expected pattern is:
#
#   synonym entailed  + antonym not entailed → claim is well-grounded
#   synonym entailed  + antonym ALSO entailed → suspicious (context is
#       too permissive — model could be reasoning in a way that admits
#       contradictions)
#   synonym not entailed → likely hallucinated (the model's claim isn't
#       even consistent with its own rephrasing)
#
# Per-claim status flows back to the chat layer as a depth annotation.
"""Metamorphic verification plugin implementation."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger("ai-companion.plugins.metamorphic")

# ── tunables ──────────────────────────────────────────────────────────
# Limit the number of factoids we score per answer. Reranking the top-N
# keeps the LLM cost bounded while covering the load-bearing claims.
MAX_FACTOIDS = 5

# Token-overlap threshold for the heuristic entailment check. Below
# this the variant is judged not-entailed by the context. Tuned against
# the test fixture; raising biases toward suspicious, lowering toward
# false-grounded.
ENTAILMENT_OVERLAP_THRESHOLD = 0.45

# Per-status weight in the aggregate score. Lower = worse signal for
# the answer-level score (1.0 is perfect, 0.0 is fully hallucinated).
_STATUS_WEIGHTS = {
    "ok": 1.0,
    "suspicious": 0.5,
    "likely_hallucinated": 0.0,
}


# ── public helpers (also imported by tests) ───────────────────────────

_NEGATION_TOKENS = {"not", "no", "never", "without", "isn't", "wasn't", "doesn't",
                    "don't", "won't", "can't", "couldn't", "shouldn't"}

_WORD = re.compile(r"\b[a-z]{2,}\b")


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


def _has_negation(s: str) -> bool:
    return bool(_NEGATION_TOKENS & _tokens(s))


def check_entailment(variant: str, context: str) -> bool:
    """Heuristic: does `variant` follow from `context`?

    Token-overlap + negation-aware. Returns True when the variant's
    content words substantially appear in the context AND there's no
    polarity flip between the two (variant negates context-supported
    statements → not entailed even with high overlap).
    """
    if not variant.strip() or not context.strip():
        return False
    vtok = _tokens(variant)
    ctok = _tokens(context)
    if not vtok:
        return False
    overlap = len(vtok & ctok) / max(1, len(vtok))
    if overlap < ENTAILMENT_OVERLAP_THRESHOLD:
        return False
    # Polarity flip: if the variant introduces a negation token that's
    # not present in context, we treat that as polarity flip → not entailed.
    if _has_negation(variant) and not _has_negation(context):
        return False
    return True


_MUTATION_PROMPT = """\
Given a factual claim, produce two mutations:
1. SYNONYM: a rephrasing that preserves the meaning (different words, same fact).
2. ANTONYM: a contradiction that flips the truth (same shape, opposite assertion).

Reply with a single JSON object: {{"synonym": "...", "antonym": "..."}}

Claim: {claim}
"""


async def generate_mutations(claim: str) -> dict[str, str]:
    """Ask the internal LLM for synonym/antonym mutations of `claim`.

    Falls back to a no-op mutation set if the LLM is unavailable (the
    scoring layer treats both mutations as entailed → no signal).
    """
    from core.utils.internal_llm import call_internal_llm

    try:
        response = await call_internal_llm([{"role": "user", "content":
            _MUTATION_PROMPT.format(claim=claim)}],
            stage="metamorphic_mutation",
        )
    except Exception as exc:  # noqa: BLE001 — LLM can fail many ways
        logger.warning("generate_mutations LLM call failed: %s", exc)
        return {"synonym": claim, "antonym": ""}

    if isinstance(response, dict):
        # Some LLM clients return parsed JSON directly
        return {
            "synonym": str(response.get("synonym", claim)),
            "antonym": str(response.get("antonym", "")),
        }

    if isinstance(response, str):
        # Strip code fences if the LLM wrapped the JSON
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
        try:
            data = json.loads(cleaned)
        except (ValueError, TypeError):
            # Try to find a JSON object inside the response
            match = re.search(r"\{[^{}]*\}", cleaned, flags=re.S)
            if match:
                try:
                    data = json.loads(match.group(0))
                except (ValueError, TypeError):
                    data = {}
            else:
                data = {}
        return {
            "synonym": str(data.get("synonym", claim)),
            "antonym": str(data.get("antonym", "")),
        }

    return {"synonym": claim, "antonym": ""}


def _classify(synonym_entailed: bool, antonym_entailed: bool) -> str:
    """Map (syn,ant) entailment results to a per-claim status."""
    if not synonym_entailed:
        return "likely_hallucinated"
    if antonym_entailed:
        return "suspicious"
    return "ok"


async def metamorphic_score(answer: str, context: str) -> dict[str, Any]:
    """Score an answer's per-claim consistency against its context.

    Returns:
        {
          "skipped": bool,            # True when feature off / no claims
          "score": float,             # 0..1, aggregate over all factoids
          "factoid_count": int,
          "suspicious_count": int,
          "details": [
            {factoid, synonym, antonym, synonym_entailed,
             antonym_entailed, status},
            …
          ]
        }
    """
    from config.features import is_feature_enabled

    if not is_feature_enabled("metamorphic_verification"):
        return {
            "skipped": True,
            "score": 1.0,
            "factoid_count": 0,
            "suspicious_count": 0,
            "details": [],
        }

    # Extract factoids using the existing claim extractor. We use the
    # heuristic path (not LLM-based) here so metamorphic scoring stays
    # additive — the LLM cost is amortized into the mutation step.
    from core.agents.hallucination.extraction import _extract_claims_heuristic

    claims = _extract_claims_heuristic(answer)
    if not claims:
        return {
            "skipped": False,
            "score": 1.0,
            "factoid_count": 0,
            "suspicious_count": 0,
            "details": [],
        }

    # Cap the per-answer factoid count
    capped = claims[:MAX_FACTOIDS]

    # Generate mutations in parallel
    mutation_tasks = [generate_mutations(c) for c in capped]
    mutations_per_claim = await asyncio.gather(*mutation_tasks, return_exceptions=False)

    details: list[dict[str, Any]] = []
    weighted_total = 0.0
    suspicious_count = 0

    for claim, mutations in zip(capped, mutations_per_claim, strict=True):
        synonym = mutations.get("synonym", "") or claim
        antonym = mutations.get("antonym", "") or ""
        syn_entailed = check_entailment(synonym, context)
        # An empty antonym is a no-signal case — treat as "antonym not
        # entailed" so it doesn't trigger the suspicious branch.
        ant_entailed = check_entailment(antonym, context) if antonym else False
        status = _classify(syn_entailed, ant_entailed)
        if status != "ok":
            suspicious_count += 1
        weighted_total += _STATUS_WEIGHTS[status]
        details.append({
            "factoid": claim,
            "synonym": synonym,
            "antonym": antonym,
            "synonym_entailed": syn_entailed,
            "antonym_entailed": ant_entailed,
            "status": status,
        })

    score = weighted_total / max(1, len(capped))
    return {
        "skipped": False,
        "score": round(score, 3),
        "factoid_count": len(capped),
        "suspicious_count": suspicious_count,
        "details": details,
    }


# ── plugin registration ───────────────────────────────────────────────

from plugins.base import CeridPlugin  # noqa: E402 — import after public helpers


class MetamorphicVerificationPlugin(CeridPlugin):
    @property
    def name(self) -> str:
        return "metamorphic_verification"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Per-claim metamorphic hallucination scoring (synonym/antonym mutations)"

    def register(self) -> None:
        """Inject the implementation into the core stub so the
        hallucination pipeline picks it up at runtime."""
        from app.agents.hallucination.metamorphic import set_metamorphic_handler

        set_metamorphic_handler(metamorphic_score)
        logger.info("metamorphic_verification plugin registered")
