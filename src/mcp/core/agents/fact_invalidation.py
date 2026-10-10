# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Contradiction-ledger routing for a superseded memory (plan Phase D, D2).

The lineage writer (``core/lineage/writer.py``) closes the old version in the
graph and in every Chroma row, and the fact writer closes the old version's
STATE facts when the new version's facts are written (spec §7: a fact closes
only once its successor's value is known). What is left here is the D2
classification: NLI decides whether the supersession was an orderly
knowledge-update, closed silently, or a genuine disagreement, which goes on
the contradiction ledger. Closure never depends on this step.
"""
from __future__ import annotations

import asyncio

import config
from core.agents.hallucination.grounding_verifier import NLI_PREMISE_CHAR_LIMIT
from core.utils.nli import batch_nli_score
from core.utils.swallowed import log_swallowed_error


async def route_supersession_to_ledger(
    *,
    old_content: str,
    new_content: str,
    old_artifact_id: str,
) -> None:
    """D2 routing: classify the closure as orderly-update vs genuine
    disagreement via NLI, and persist genuine disagreements to the
    contradiction ledger (mirrors ``verification.py`` sink invocation).

    Best-effort throughout: NLI failure or a missing sink leaves the (already
    applied) closure intact — closure must never depend on NLI availability.
    The ledger is the only consumer of the NLI signal here, so when it is
    disabled the classification is skipped entirely.
    """
    if not config.ENABLE_CONTRADICTION_LEDGER:
        return

    old_slice = (old_content or "")[:NLI_PREMISE_CHAR_LIMIT]
    new_slice = (new_content or "")[:NLI_PREMISE_CHAR_LIMIT]
    if not old_slice.strip() or not new_slice.strip():
        return  # nothing to classify — orderly closure only

    try:
        scores = await asyncio.to_thread(batch_nli_score, [(old_slice, new_slice)])
    except Exception as exc:  # noqa: BLE001 — closure must not depend on NLI
        log_swallowed_error("core.agents.fact_invalidation.nli", exc)
        return

    contradiction = float(scores[0]["contradiction"]) if scores else 0.0
    if contradiction < config.NLI_CONTRADICTION_THRESHOLD:
        return  # orderly knowledge-update — closure only, no ledger entry

    # Genuine disagreement — surface on the contradiction ledger. Gated +
    # best-effort; the sink is wired from app startup (core/ cannot import
    # app.services.contradiction_log). Mirrors verification.py's invocation.
    from core.agents.hallucination.contradiction_sink import get_contradiction_sink

    _csink = get_contradiction_sink()
    if _csink is not None:
        try:
            await _csink(
                claim_text=new_content[:500],
                source_text=old_content[:500],
                source_artifact_id=old_artifact_id,
                severity="medium",
            )
        except Exception as exc:  # noqa: BLE001 — ledger write must not block closure
            log_swallowed_error(
                "core.agents.fact_invalidation.contradiction_sink", exc
            )
