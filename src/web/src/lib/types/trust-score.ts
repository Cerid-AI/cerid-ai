// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * TypeScript types mirroring the backend Pydantic models in
 * src/mcp/app/services/trust_score.py.
 *
 * These are pure presentation types — they do not affect retrieval,
 * generation, or any model decision.
 */

export type ComponentStatus = "ok" | "warn" | "fail" | "not_available"
export type ScoreBand = "high" | "medium" | "low"

export interface TrustComponent {
  /** Stable machine id, e.g. "faithfulness", "retrieval_ndcg10". */
  id: string
  /** Human-readable label, e.g. "Faithfulness". */
  label: string
  /** Raw measurement value (e.g. 0.93 for faithfulness), or null if unavailable. */
  value: number | null
  /** Minimum acceptable value, or null when no target is set. */
  target: number | null
  /** Value normalized to [0, 1] for averaging, or null if unavailable. */
  normalized: number | null
  /** Whether the component is meeting its target. */
  status: ComponentStatus
  /** Human-readable source description, e.g. "nightly RAGAS". */
  source: string
  /** ISO-8601 timestamp of the last measurement. */
  last_updated_at: string | null
  /** Optional note (e.g. why a value is missing). */
  note: string | null
}

export interface TrustScore {
  /** Composite score 0–100, or null when no components have data. */
  score: number | null
  /** Band describing the score range. */
  band: ScoreBand | null
  /** ISO-8601 timestamp of when this score was computed. */
  updated_at: string
  /** All component details, including unavailable ones. */
  components: TrustComponent[]
  /** Methodology note surfaced from the backend. */
  note?: string
}

/** Color band metadata for rendering. */
export interface BandDisplay {
  band: ScoreBand | null
  /** Tailwind text color class. */
  textClass: string
  /** Tailwind background color class. */
  bgClass: string
  /** Tailwind border color class. */
  borderClass: string
  /** Human-readable band label. */
  label: string
}

/** Derive display metadata for a score band. */
export function getBandDisplay(band: ScoreBand | null): BandDisplay {
  switch (band) {
    case "high":
      return {
        band,
        textClass: "text-green-700 dark:text-green-400",
        bgClass: "bg-green-500/15",
        borderClass: "border-green-500/30",
        label: "high",
      }
    case "medium":
      return {
        band,
        textClass: "text-amber-700 dark:text-amber-400",
        bgClass: "bg-amber-500/15",
        borderClass: "border-amber-500/30",
        label: "medium",
      }
    case "low":
      return {
        band,
        textClass: "text-red-700 dark:text-red-400",
        bgClass: "bg-red-500/15",
        borderClass: "border-red-500/30",
        label: "low",
      }
    default:
      return {
        band: null,
        textClass: "text-muted-foreground",
        bgClass: "bg-muted",
        borderClass: "border-border",
        label: "unavailable",
      }
  }
}

/** Per-component explainer content for the modal. */
export interface ComponentMeta {
  id: string
  docsHref: string
  calculation: string
  whenDrops: string
}

export const COMPONENT_META: Record<string, ComponentMeta> = {
  faithfulness: {
    id: "faithfulness",
    docsHref: "docs/EVAL_BASELINES.md#faithfulness",
    calculation:
      "Nightly RAGAS evaluation on a golden question set. Measures whether each generated answer is supported by the retrieved context. Score ≥ 0.90 is the target.",
    whenDrops:
      "Retrieval context may be diverging from answers; review recent ingestion quality and re-run RAGAS baseline.",
  },
  retrieval_ndcg10: {
    id: "retrieval_ndcg10",
    docsHref: "docs/EVAL_BASELINES.md#retrieval-ndcg10",
    calculation:
      "Nightly IR evaluation against the stored baseline. NDCG@10 measures how well the top-10 retrieved chunks rank ground-truth relevant chunks. Target: ≥ stored baseline.",
    whenDrops:
      "Index quality or retrieval parameters may have regressed; check ChromaDB and reranker health.",
  },
  memory_recall: {
    id: "memory_recall",
    docsHref: "docs/EVAL_BASELINES.md#memory-recall",
    calculation:
      "Weekly LongMemEval run over the memory store. Measures recall of facts stored in long-term memory across multi-turn sessions. Target: ≥ 0.80.",
    whenDrops:
      "Memory consolidation may have lost entries; inspect the consolidation log and run the weekly eval manually.",
  },
  verification_coverage: {
    id: "verification_coverage",
    docsHref: "docs/PRESERVATION.md#verification-coverage",
    calculation:
      "Rolling 24-hour fraction of claims in the graph that have at least one provenance source attached. Target: ≥ 95%.",
    whenDrops:
      "NLI verification or source-linking may be failing silently; check `/health.swallowed_errors_last_hour`.",
  },
  preservation_health: {
    id: "preservation_health",
    docsHref: "docs/PRESERVATION.md",
    calculation:
      "Ratio of passing to total preservation invariant tests from the last main CI run. Target: all 35+ gates green (1.0).",
    whenDrops:
      "A preservation invariant regressed; run `make preservation-check` locally to identify the failing gate.",
  },
  user_agreement: {
    id: "user_agreement",
    docsHref: "docs/EVAL_BASELINES.md#user-agreement",
    calculation:
      "Rolling 7-day ratio of positive to total user feedback signals (thumbs up/down). Target: ≥ 0.80. (Phase R.1 — data available after feedback loop ships.)",
    whenDrops:
      "User-facing quality is declining; review recent answer quality and retrieval config.",
  },
}
