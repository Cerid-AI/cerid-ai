# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Pluggable grounding-verifier tier (Phase 3.1).

Abstracts the entailment/contradiction scoring the verification pipeline uses to
decide whether evidence *grounds* a claim. The default implementation delegates
to the deberta-xsmall ONNX NLI in :mod:`core.utils.nli` (behavior unchanged); a
MiniCheck-class grounding model can register an alternate and be selected by name
without touching any verification call site.

Scoping: this is the interface + default + config seam only. Serving an actual
MiniCheck-7B (via quenchforge) is owner-gated infrastructure and is NOT wired
here — the registry is the plumbing a future integration slots into.

The selected verifier name is read from ``config.GROUNDING_VERIFIER`` when that
setting exists (defaulting to :data:`_DEFAULT_VERIFIER_NAME` otherwise), so wiring
an alternate is a one-line settings addition + a ``register_grounding_verifier``
call, not a refactor.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import config

# Char ceiling on the evidence premise handed to the grounding verifier. The
# deberta NLI tokenizer truncates to 512 TOKENS (~2000 chars for English); the
# pre-3.1 call sites sliced the premise to 512 *chars* first, a second ~4×
# tighter ceiling that starved the model of ~three-quarters of the evidence it
# was built to read. Sized to the tokenizer's token budget so the tokenizer —
# not an arbitrary char slice — performs the real truncation.
NLI_PREMISE_CHAR_LIMIT = 2000

# Registry default. Kept as a module constant (not a settings knob) so the
# no-op-when-unset getattr read below has a single source of truth.
_DEFAULT_VERIFIER_NAME = "nli_deberta"


@runtime_checkable
class GroundingVerifier(Protocol):
    """Scores how a piece of evidence bears on a claim.

    Implementations return the same dict shape as :func:`core.utils.nli.nli_score`
    — ``{"entailment", "contradiction", "neutral", "label"}`` — so the verdict
    branches consume any verifier identically.
    """

    name: str

    async def score(self, premise: str, hypothesis: str) -> dict[str, Any]:
        """Return entailment/contradiction/neutral probabilities for the pair."""
        ...


class NliDebertaVerifier:
    """Default grounding verifier — the deberta-xsmall ONNX NLI.

    Imports :func:`core.utils.nli.nli_score_async` *at call time* (not at module
    load) so the batching coalescer is used and so existing tests that patch
    ``core.utils.nli.nli_score_async`` keep hitting the real call site.
    """

    name = _DEFAULT_VERIFIER_NAME

    async def score(self, premise: str, hypothesis: str) -> dict[str, Any]:
        from core.utils.nli import nli_score_async

        return await nli_score_async(premise, hypothesis)


_REGISTRY: dict[str, GroundingVerifier] = {}


def register_grounding_verifier(verifier: GroundingVerifier) -> None:
    """Register *verifier* under its ``name`` (last registration wins)."""
    _REGISTRY[verifier.name] = verifier


def get_grounding_verifier() -> GroundingVerifier:
    """Return the configured grounding verifier.

    Selection order: ``config.GROUNDING_VERIFIER`` (when the setting exists) →
    :data:`_DEFAULT_VERIFIER_NAME`. An unknown name falls back to the default
    rather than raising, so a stale config value degrades to the proven NLI path
    instead of breaking verification.
    """
    name = getattr(config, "GROUNDING_VERIFIER", _DEFAULT_VERIFIER_NAME)
    return _REGISTRY.get(name) or _REGISTRY[_DEFAULT_VERIFIER_NAME]


# Register the default at import so ``get_grounding_verifier`` always resolves.
register_grounding_verifier(NliDebertaVerifier())
