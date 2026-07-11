# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Adaptive configuration recommendation registry (Cycle 3.2).

Cerid ships several retrieval features that are flag-gated by default
because they only pay off once a corpus reaches a certain size. Until
v0.93.3 the operator had to read the docs and flip env vars by hand —
this module is the registry the new in-app recommender consumes to
surface "your corpus has grown, here's the next setting to consider"
banners in the Settings pane.

Design decisions (locked):

* **Enable-only** — the recommender never suggests turning a feature off.
  Reducing-config nudges land in a future cycle if at all.
* **One-way per recommendation** — a feature appears at most once in
  the registry, with a single threshold + reason.
* **Pure, side-effect-free** — every entry is data; no imports of
  ``app.*``, no I/O. The :class:`ConfigRecommenderJob` in
  ``app/processor/jobs/config_recommender.py`` is the (single) consumer.

Adding a new recommendation:

1. Add an entry to :data:`RECOMMENDATIONS`.
2. Pick the threshold env var (so the operator can tune without code).
3. Decide which ``enable_payload`` the Settings PATCH should accept
   when the user clicks "Enable now" — that wire-up lives in
   ``app/routers/settings.py``.
4. Optionally add a 4th-position field for non-corpus-size conditions
   (e.g. "only if total docs include code").
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CorpusStats:
    """Snapshot of corpus health used to evaluate recommendation conditions.

    Kept intentionally small — anything the job can't compute in a
    single Cypher query doesn't belong here yet. Future revisions can
    add fields without breaking the registry contract.
    """

    artifact_count: int
    """Distinct non-eval-corpus Artifacts in Neo4j."""

    flags_enabled: frozenset[str]
    """Names of retrieval flags currently flipped on (e.g. ``"RETRIEVAL_HYPE_ENABLED"``)."""

    longest_conversation_length: int = 0
    """Message count of the operator's longest chat thread.

    Sourced from the same Neo4j query the recommender job runs; default
    0 keeps existing tests working without rewiring fixtures.  Used by
    the chat-virtualization recommendation to surface only once a
    conversation is large enough to benefit.
    """


@dataclass(frozen=True, slots=True)
class RecommendationSpec:
    """One row in the registry — wholly declarative.

    ``condition_fn`` is the only callable; everything else is data so
    the registry can be inspected in tests without monkey-patching.
    """

    id: str
    """Stable identifier surfaced to the frontend as the banner key."""

    label: str
    """Short human-readable name shown in the banner heading."""

    flag_env_var: str
    """The env var the operator currently sets (e.g. ``RETRIEVAL_SPARSE_ENABLED``)."""

    enable_payload: dict[str, Any] = field(default_factory=dict)
    """Body the Settings PATCH receives when the user clicks "Enable now"."""

    reason_template: str = ""
    """Short rationale; ``{count}`` is substituted with the live corpus size."""

    condition_fn: Callable[[CorpusStats], bool] = field(default=lambda _: False)
    """Returns True when this recommendation should fire."""


# ---------------------------------------------------------------------------
# Threshold env vars
# ---------------------------------------------------------------------------
#
# Each registry entry reads its corpus-size threshold from an env var
# so an operator can tune without code. The defaults track the C1 eval
# ledger and the C3.2 sparse-retrieval thresholds in the SPLADE-v3
# paper; raise / lower per your corpus.

_THRESHOLD_SPARSE = int(os.getenv("CERID_RECOMMEND_SPARSE_AT", "100"))
_THRESHOLD_HYPE = int(os.getenv("CERID_RECOMMEND_HYPE_AT", "100"))
_THRESHOLD_PARENT_CHILD = int(os.getenv("CERID_RECOMMEND_PARENT_CHILD_AT", "100"))
_THRESHOLD_RRF = int(os.getenv("CERID_RECOMMEND_RRF_AT", "500"))
# Chat-virtualization threshold uses message count, not artifact count,
# since the relevant dimension is conversation length.  200 is the
# breakpoint where plain .map() reconciliation starts costing on every
# streaming token, per the sprint plan's perf rationale.
_THRESHOLD_VIRTUALIZATION = int(os.getenv("CERID_RECOMMEND_VIRTUALIZATION_AT", "200"))


def _at(n: int, flag: str) -> Callable[[CorpusStats], bool]:
    """Build a condition that fires when corpus ≥ ``n`` AND ``flag`` is off."""

    def _cond(stats: CorpusStats) -> bool:
        return stats.artifact_count >= n and flag not in stats.flags_enabled

    return _cond


def _sparse_at(n: int, flag: str) -> Callable[[CorpusStats], bool]:
    """Sparse-retrieval condition: corpus threshold AND flag off AND an
    encode path that could actually run (V1 Task 4.3 — enabling the flag
    on a deployment with no sidecar and no in-process deps is a silent
    no-op, and the card must not recommend one)."""

    base = _at(n, flag)

    def _cond(stats: CorpusStats) -> bool:
        if not base(stats):
            return False
        from core.retrieval.sparse import encode_path_available

        return encode_path_available()

    return _cond


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RECOMMENDATIONS: tuple[RecommendationSpec, ...] = (
    RecommendationSpec(
        id="sparse_retrieval",
        label="SPLADE-v3 sparse retrieval",
        flag_env_var="RETRIEVAL_SPARSE_ENABLED",
        enable_payload={"enable_sparse_retrieval": True, "hybrid_fusion_mode": "tri_rrf"},
        reason_template=(
            "Your corpus is now {count} documents. SPLADE-v3 catches "
            "synonym matches that BM25 and dense vectors miss; turning it "
            "on adds a third retriever and fuses all three via RRF."
        ),
        condition_fn=_sparse_at(_THRESHOLD_SPARSE, "RETRIEVAL_SPARSE_ENABLED"),
    ),
    RecommendationSpec(
        id="hype_indexing",
        label="Hypothetical Prompt Embeddings (HyPE)",
        flag_env_var="RETRIEVAL_HYPE_ENABLED",
        enable_payload={"enable_hype": True},
        reason_template=(
            "At {count} documents your corpus is large enough for HyPE's "
            "question-to-chunk index to pay off — it cuts top-1 miss rate "
            "by ~6% on the Cerid retrieval eval."
        ),
        condition_fn=_at(_THRESHOLD_HYPE, "RETRIEVAL_HYPE_ENABLED"),
    ),
    RecommendationSpec(
        id="parent_child_retrieval",
        label="Parent-child chunk retrieval",
        flag_env_var="PARENT_CHILD_ENABLED",
        enable_payload={"enable_parent_child_retrieval": True},
        reason_template=(
            "With {count} documents indexed, parent-child retrieval lets "
            "the model match against small, precise chunks while still "
            "seeing the surrounding paragraph at generation time."
        ),
        condition_fn=_at(_THRESHOLD_PARENT_CHILD, "PARENT_CHILD_ENABLED"),
    ),
    RecommendationSpec(
        id="rrf_fusion",
        label="Reciprocal Rank Fusion (RRF)",
        flag_env_var="HYBRID_FUSION_MODE",
        enable_payload={"hybrid_fusion_mode": "rrf"},
        reason_template=(
            "Past {count} documents, RRF's chunk-rank fusion outperforms "
            "the legacy weighted-sum blend on Cerid's eval corpus — "
            "Elastic, OpenSearch, and Azure AI Search all default to it."
        ),
        # RRF needs HYBRID_FUSION_MODE != "weighted_sum" to count as on.
        # We treat the "weighted_sum" string as the disabled state so any
        # rrf/tri_rrf value satisfies the flag.
        condition_fn=lambda stats: (
            stats.artifact_count >= _THRESHOLD_RRF
            and "HYBRID_FUSION_MODE_ACTIVE" not in stats.flags_enabled
        ),
    ),
    RecommendationSpec(
        id="chat_virtualization",
        label="Virtualized chat list (long conversations)",
        flag_env_var="ENABLE_CHAT_VIRTUALIZATION",
        # The flag is consumed client-side via localStorage
        # (``cerid:chat-virtualized``); the enable_payload tells the
        # frontend recommendation banner which key to write.  No
        # backend setting is mutated because virtualization is a pure
        # render-tree choice.
        enable_payload={"enable_chat_virtualization": True},
        reason_template=(
            "One of your conversations has {count} messages.  Virtualization "
            "keeps the chat pane responsive at that length by rendering only "
            "the visible window instead of the whole transcript."
        ),
        # Uses the new longest_conversation_length field; falls back to 0
        # for callers that don't yet populate it (existing tests).
        condition_fn=lambda stats: (
            stats.longest_conversation_length >= _THRESHOLD_VIRTUALIZATION
            and "ENABLE_CHAT_VIRTUALIZATION" not in stats.flags_enabled
        ),
    ),
)


def evaluate(stats: CorpusStats) -> list[tuple[RecommendationSpec, str]]:
    """Walk the registry and return the (spec, formatted_reason) pairs that fire.

    Pure function — no I/O. Tests can call this with hand-built
    :class:`CorpusStats` and assert the expected list shape.
    """
    result: list[tuple[RecommendationSpec, str]] = []
    for spec in RECOMMENDATIONS:
        if spec.condition_fn(stats):
            reason = spec.reason_template.format(count=stats.artifact_count)
            result.append((spec, reason))
    return result
