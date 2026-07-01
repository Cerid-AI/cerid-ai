# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical enums for the verification / NLI layer.

Before this module the verification code carried two kinds of magic strings:

* verdict values (``"verified"`` / ``"unverified"`` / ``"uncertain"`` / …) —
  duplicated across ``verification.py``, ``streaming.py`` and ``self_rag.py``;
* NLI call *purpose* — implicit in which function ran ``nli_score`` (the KB
  gate, the cited-URL check, the CRAG coverage loop, the retrieval gate, the
  authoritative cross-validation), never named.

``VerificationStatus`` is the single canonical verdict enum. It aliases the
existing :class:`~core.agents.hallucination.models.ClaimStatus` so there is
exactly one source of truth — new code imports ``VerificationStatus``; the
Pydantic model layer keeps using ``ClaimStatus`` (same object).

``NLIUse`` names the entailment call sites so a shared verifier can pick the
right threshold band / prompt strategy per use instead of an ad-hoc if-chain.
"""

from __future__ import annotations

from enum import Enum

from core.agents.hallucination.models import ClaimStatus

# One canonical verdict enum. ClaimStatus already carries the right five values
# (verified / unverified / uncertain / skipped / error); alias rather than
# duplicate so the model layer and the verification logic never drift.
VerificationStatus = ClaimStatus


class NLIUse(str, Enum):
    """Why an NLI entailment call is being made.

    Lets one verifier apply use-appropriate thresholds/behavior instead of the
    caller-specific fallback chains that grew independently across the layer.
    """

    SYNTHESIS_GATE = "synthesis_gate"      # inline, mid-stream sentence gating
    KB_GATE = "kb_gate"                     # verify_claim primary KB NLI gate
    CITED_URL = "cited_url"                 # claim vs the LLM-cited page body
    CRAG_GATE = "crag_gate"                 # self-RAG retrieval-coverage check
    RETRIEVAL_GATE = "retrieval_gate"       # NLI gate on retrieval results
    AUTHORITATIVE_GATE = "authoritative_gate"  # per authoritative external source
    CROSS_VALIDATE = "cross_validate"       # KB evidence vs external evidence
    MEMORY_GATE = "memory_gate"             # memory contradiction detection
    RAGAS_EVAL = "ragas_eval"               # offline faithfulness eval
    EXT_PROMPT_HINT = "ext_prompt_hint"     # KB-snippet pre-score for verifier prompt


__all__ = ["NLIUse", "VerificationStatus"]
