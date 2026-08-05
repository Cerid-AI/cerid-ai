# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Stage-profile registry — dynamic model selection by (task_type, hardness).

Every ``call_internal_llm(..., stage="...")`` site is classified by what the
LLM is being asked to do (``TaskType``) and how hard the task is
(``Hardness``).  The resolver maps ``(task_type, hardness)`` → one of the
existing tier keys in ``ACTIVE_MODELS["tiers"]`` so we don't introduce a
parallel model catalog.

Lookup order in ``core.utils.internal_llm._resolve_stage_model``:

1. ``PROVIDER_STAGE_<NORMALIZED_STAGE>_MODEL`` env var — operator pin for a
   specific stage. Wins everything. Use the full ``openrouter/<provider>/<id>``
   form so it bypasses upstream routing.
2. ``STAGE_PROFILES[stage]`` → ``HARDNESS_TO_TIER[hardness]`` → tier model
   from the registry. This is the **smart default** — RAGAS judges land on
   a moderate model, summary stages on a simple model, frontier generation
   on the user's general-role pick.
3. No match → empty string. The caller's existing fallback chain
   (``INTERNAL_LLM_MODEL`` env, ``_DEFAULT_INTERNAL_MODEL``) takes over.

The *hardness ladder* maps to the existing ``ACTIVE_MODELS["tiers"]`` keys so
all knobs already exposed via ``/models/assignments`` still work:

==========  =====================  ====================================
Hardness    Tier in registry       Typical model (defaults shipped)
==========  =====================  ====================================
TRIVIAL     ``free``               llama-3.3-70b-instruct:free
SIMPLE      ``cheap``              gpt-4o-mini class
MODERATE    ``research``           grok-4.x class (capable + cheap + fast)
HARD        ``capable``            claude-sonnet-4.6 class (reasoning)
FRONTIER    ``expert``             claude-opus class
==========  =====================  ====================================

Why MODERATE lands on ``research`` (grok-4.x) rather than ``capable``
(sonnet): grok-4.3 priced at ~$0.20/$0.50 per MTok is ~30× cheaper than
sonnet on output and meaningfully faster — and Wave-2 (2026-06-12)
showed sonnet judging hit the verify-request 120s budget on long
answers. Sonnet stays available for HARD where the reasoning depth
actually pays for itself; the operator pin path is one env var away
for any stage that wants to override this default.

**User pins** — the cerid-ai operator has three knobs:

- Per-stage env pin: ``PROVIDER_STAGE_FAITHFULNESS_DECOMPOSE_MODEL=...``
- Per-tier override: ``/models/assignments`` PUT with a new tier mapping
- Edit ``STAGE_PROFILES`` to retag a stage's hardness (code change)

The profiles below are the **classification**, not the policy: which model
a hardness maps to is policy and lives in the registry, not here.
"""

from __future__ import annotations

import enum
import os


class TaskType(str, enum.Enum):
    """What the LLM is being asked to do.

    Not all task types are currently distinguishable by model selection
    (Hardness does most of the dispatch work today), but the dimension is
    here so a future smart-router can pick e.g. a code-tuned vs prose-tuned
    model of the same hardness.
    """

    GENERATION = "generation"           # user-facing chat / RAG answer
    REASONING = "reasoning"             # multi-step inference, Cypher gen
    JUDGING = "judging"                 # RAGAS metrics / verification scoring
    EXTRACTION = "extraction"           # entities, claims, structured fields
    DECOMPOSITION = "decomposition"     # break question / claim into pieces
    SUMMARIZATION = "summarization"     # wiki / digest / artifact / domain
    CLASSIFICATION = "classification"   # topic / intent / label
    RERANKING = "reranking"             # LLM-as-reranker
    INDEXING = "indexing"               # HyPE hypothetical-doc generation


class Hardness(str, enum.Enum):
    """How capable a model needs to be for this stage to produce useful output."""

    TRIVIAL = "trivial"      # rule-of-thumb categorization, label maps
    SIMPLE = "simple"        # short summaries, structured rewrites
    MODERATE = "moderate"    # judging, decomposition, careful extraction
    HARD = "hard"            # complex reasoning, expert verification
    FRONTIER = "frontier"    # user-facing generation; quality matters most


HARDNESS_TO_TIER: dict[Hardness, str] = {
    Hardness.TRIVIAL: "free",
    Hardness.SIMPLE: "cheap",
    # MODERATE: judging / decomposition / extraction stages — picked grok-4.x
    # class (research tier) over sonnet (capable tier) so the default is
    # cost-light + latency-light. See module docstring for the rationale.
    Hardness.MODERATE: "research",
    # HARD: complex reasoning (Cypher gen, deep verification). Sonnet's
    # extra rigor pays for itself; this is the bucket where the higher
    # token cost is worth it.
    Hardness.HARD: "capable",
    Hardness.FRONTIER: "expert",
}


# Stage → (task_type, hardness). Add new stages here as they're introduced.
# The list mirrors the stages already in use across the codebase (see
# ``rg "stage=" src/mcp/`` for the live set). Stages not in this map fall
# through to the caller's INTERNAL_LLM_MODEL default.
STAGE_PROFILES: dict[str, tuple[TaskType, Hardness]] = {
    # --- User-facing generation (frontier) ---
    "mcp_answer_with_citations": (TaskType.GENERATION, Hardness.FRONTIER),
    "brief": (TaskType.GENERATION, Hardness.FRONTIER),
    # --- Reasoning ---
    "longshot_cypher": (TaskType.REASONING, Hardness.HARD),
    # --- Judging (RAGAS metrics + verification scoring) ---
    "faithfulness/decompose": (TaskType.DECOMPOSITION, Hardness.MODERATE),
    "faithfulness/score": (TaskType.JUDGING, Hardness.MODERATE),
    "context_precision": (TaskType.JUDGING, Hardness.MODERATE),
    "context_recall": (TaskType.JUDGING, Hardness.MODERATE),
    "answer_relevancy": (TaskType.JUDGING, Hardness.MODERATE),
    # --- Decomposition / extraction ---
    "query_decompose": (TaskType.DECOMPOSITION, Hardness.MODERATE),
    "mcp_question_decompose": (TaskType.DECOMPOSITION, Hardness.MODERATE),
    "claim_extraction": (TaskType.EXTRACTION, Hardness.MODERATE),
    "entity_extraction": (TaskType.EXTRACTION, Hardness.MODERATE),
    "memory_extract": (TaskType.EXTRACTION, Hardness.MODERATE),
    # --- Summarization (cheap by default; bump to MODERATE if quality regresses) ---
    "curator_synopsis": (TaskType.SUMMARIZATION, Hardness.SIMPLE),
    "mcp_summarize_artifact": (TaskType.SUMMARIZATION, Hardness.SIMPLE),
    "mcp_summarize_domain": (TaskType.SUMMARIZATION, Hardness.SIMPLE),
    "wiki_summary": (TaskType.SUMMARIZATION, Hardness.SIMPLE),
    "community_summary": (TaskType.SUMMARIZATION, Hardness.SIMPLE),
    "daily_digest": (TaskType.SUMMARIZATION, Hardness.SIMPLE),
    "contextual_chunks": (TaskType.SUMMARIZATION, Hardness.MODERATE),
    # --- Classification ---
    "hallucination_topic": (TaskType.CLASSIFICATION, Hardness.TRIVIAL),
    "inbox_triage": (TaskType.CLASSIFICATION, Hardness.SIMPLE),
    # --- Reranking ---
    "assembler_rerank": (TaskType.RERANKING, Hardness.SIMPLE),
    "rerank_llm": (TaskType.RERANKING, Hardness.SIMPLE),
    # --- Indexing ---
    "hype_index": (TaskType.INDEXING, Hardness.MODERATE),
    "mcp_hypothetical_doc": (TaskType.INDEXING, Hardness.MODERATE),
    # --- Memory / workers ---
    "memory_consolidation": (TaskType.SUMMARIZATION, Hardness.MODERATE),
    "memory_conflict_resolve": (TaskType.JUDGING, Hardness.MODERATE),
    "memory_worker": (TaskType.EXTRACTION, Hardness.MODERATE),
    "ingest_worker": (TaskType.EXTRACTION, Hardness.MODERATE),
}


def _normalize_stage(stage: str) -> str:
    """``"faithfulness/decompose"`` → ``"FAITHFULNESS_DECOMPOSE"``."""
    return stage.upper().replace("/", "_").replace("-", "_")


def env_pin_for(stage: str) -> str | None:
    """Read ``PROVIDER_STAGE_<NORMALIZED_STAGE>_MODEL`` env override.

    Returns the literal model id string when set, else ``None``. The model
    id is passed verbatim to the LLM client — use the full
    ``openrouter/<provider>/<id>`` form for OpenRouter pins.
    """
    if not stage:
        return None
    pinned = os.environ.get(f"PROVIDER_STAGE_{_normalize_stage(stage)}_MODEL")
    return pinned or None


def _profile_for(stage: str) -> tuple[TaskType, Hardness] | None:
    """Look up a stage's profile, falling back to the part before the first "/"
    for sub-stages (E1 CR-014: live call sites use slashed sub-stage names like
    "brief/daily" and "hype_index/generate" that the exact-only lookup missed,
    silently dropping their tier to the INTERNAL_LLM_MODEL default). Exact match
    wins so explicit sub-stage profiles (e.g. faithfulness/decompose) are kept."""
    profile = STAGE_PROFILES.get(stage)
    if profile is None and "/" in stage:
        profile = STAGE_PROFILES.get(stage.split("/", 1)[0])
    return profile


def hardness_for(stage: str) -> Hardness | None:
    """Return the classified hardness for a stage, or ``None`` if unknown."""
    profile = _profile_for(stage)
    return profile[1] if profile else None


def task_type_for(stage: str) -> TaskType | None:
    """Return the classified task type for a stage, or ``None`` if unknown."""
    profile = _profile_for(stage)
    return profile[0] if profile else None


def tier_for(stage: str) -> str | None:
    """Return the ``ACTIVE_MODELS["tiers"]`` key for the stage, or ``None``.

    Wraps ``hardness_for`` → ``HARDNESS_TO_TIER`` so the resolver in
    ``core.utils.internal_llm`` doesn't need to import the enums directly.
    """
    h = hardness_for(stage)
    return HARDNESS_TO_TIER.get(h) if h else None
