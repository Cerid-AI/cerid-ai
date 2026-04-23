# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Metamorphic verification plugin — perturbation-based hallucination detection.

Extracts atomic factoids, generates synonym/antonym mutations, then checks
entailment against source context.  Penalty when synonym NOT entailed or
antonym IS entailed.  Pro tier only (``metamorphic_verification`` feature flag).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.utils.llm_parsing import parse_llm_json

__all__ = [
    "check_entailment",
    "generate_mutations",
    "metamorphic_score",
    "register",
]

logger = logging.getLogger(__name__)

_MAX_FACTOIDS = 5
_ENTAILMENT_THRESHOLD = 0.40

# Stopwords excluded from overlap computation.
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could of in to for on with "
    "at by from as into about between through after before and but or "
    "not no nor so yet if then than that this it its".split()
)

_MUTATION_PROMPT = (
    'Given this factoid: "{factoid}"\n'
    "Generate two variants:\n"
    "1. SYNONYM: Rephrase with different words but identical meaning\n"
    "2. ANTONYM: Change one key fact to make it false\n"
    'Respond as JSON: {{"synonym": "...", "antonym": "..."}}'
)


def _content_words(text: str) -> set[str]:
    """Extract lowercase content words (non-stopwords, len >= 2)."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in tokens if w not in _STOPWORDS and len(w) >= 2}


def check_entailment(variant: str, context: str) -> bool:
    """Return True when >40% of variant's content words appear in *context*."""
    variant_words = _content_words(variant)
    if not variant_words:
        return False
    context_words = _content_words(context)
    overlap = len(variant_words & context_words)
    return (overlap / len(variant_words)) > _ENTAILMENT_THRESHOLD


async def generate_mutations(factoid: str) -> dict[str, str]:
    """Return ``{"synonym": ..., "antonym": ...}``; falls back on LLM failure."""
    from core.utils.internal_llm import call_internal_llm

    prompt = _MUTATION_PROMPT.format(factoid=factoid)
    try:
        raw = await call_internal_llm(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
            stage="metamorphic_mutation",
        )
        parsed: dict[str, Any] = parse_llm_json(raw)
        return {
            "synonym": str(parsed.get("synonym", factoid)),
            "antonym": str(parsed.get("antonym", "")),
        }
    except Exception:
        logger.debug("Mutation generation failed for factoid, using fallback")
        return {"synonym": factoid, "antonym": ""}


async def metamorphic_score(answer: str, context: str) -> dict[str, Any]:
    """Score *answer* trustworthiness via metamorphic perturbation testing."""
    from config.features import is_feature_enabled

    if not is_feature_enabled("metamorphic_verification"):
        return {
            "score": 1.0,
            "factoid_count": 0,
            "suspicious_count": 0,
            "details": [],
            "skipped": True,
        }

    from core.agents.hallucination.extraction import _extract_claims_heuristic

    factoids = _extract_claims_heuristic(answer)[:_MAX_FACTOIDS]
    if not factoids:
        return {
            "score": 1.0,
            "factoid_count": 0,
            "suspicious_count": 0,
            "details": [],
        }

    details: list[dict[str, Any]] = []
    suspicious = 0

    for factoid in factoids:
        mutations = await generate_mutations(factoid)

        syn_entailed = check_entailment(mutations["synonym"], context)
        ant_entailed = check_entailment(mutations["antonym"], context) if mutations["antonym"] else False

        if not syn_entailed and ant_entailed:
            status = "likely_hallucinated"
            suspicious += 1
        elif not syn_entailed or ant_entailed:
            status = "suspicious"
            suspicious += 1
        else:
            status = "ok"

        details.append({
            "factoid": factoid,
            "synonym_entailed": syn_entailed,
            "antonym_entailed": ant_entailed,
            "status": status,
        })

    # Score: each factoid starts at 1.0 points.
    # synonym_not_entailed => -0.5, antonym_entailed => -0.5
    total = len(factoids)
    penalty = 0.0
    for d in details:
        if not d["synonym_entailed"]:
            penalty += 0.5
        if d["antonym_entailed"]:
            penalty += 0.5
    score = max(0.0, 1.0 - (penalty / total))

    return {
        "score": round(score, 4),
        "factoid_count": total,
        "suspicious_count": suspicious,
        "details": details,
    }


def register() -> None:
    """Register the metamorphic verifier with the hallucination pipeline.

    Sets the module-level handler in the core stub so that
    ``streaming.py`` can delegate to this implementation.
    """
    from app.agents.hallucination.metamorphic import set_metamorphic_handler
    set_metamorphic_handler(metamorphic_score)
    logger.info("Metamorphic verification plugin registered")
