// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Reader + renderer for the ``inference_routing`` block carried by ``/health``
 * and ``/health/status``.
 *
 * The backend builds it in ``core/utils/inference_routing.get_routing_snapshot``
 * (configured intent) and annotates each lane with the live serving state
 * recorded by ``core/utils/inference_health`` — which provider actually
 * answered, whether it is a fallback, why, and how many times. That is the only
 * place the GUI can learn that the GPU rerank lane is dead while every
 * transport probe is green.
 *
 * ``HealthStatusResponse.inference_routing`` is typed ``Record<string, unknown>``
 * (owned elsewhere), so the narrowing lives here and every consumer reads lanes
 * through it rather than re-deriving the shape.
 */

import { cn } from "@/lib/utils"

export const INFERENCE_LANES = ["llm", "embed", "rerank", "sparse", "nli"] as const
export type InferenceLaneName = (typeof INFERENCE_LANES)[number]

export interface InferenceLane {
  lane: InferenceLaneName
  /** Configured provider — operator intent. */
  provider: string
  /** Configured model. The backend emits the literal "unset" when none is pinned. */
  model: string
  /** What actually answered last: a provider id, a fallback runtime, or "unknown". */
  serving: string
  /** Model name the backend reported on its last response, when it reports one. */
  servingModel: string
  degraded: boolean
  degradedDetail: string
  fallbackCount: number
}

const LANE_LABEL: Record<InferenceLaneName, string> = {
  llm: "LLM",
  embed: "Embeddings",
  rerank: "Reranking",
  sparse: "Sparse",
  nli: "NLI",
}

const PROVIDER_LABEL: Record<string, string> = {
  quenchforge: "Quenchforge",
  ollama: "Ollama",
  openrouter: "OpenRouter",
  sidecar: "Sidecar",
  "in-process": "In-process",
  onnx: "ONNX (CPU)",
  disabled: "Disabled",
  unknown: "Unknown",
}

/**
 * Serving runtimes that still execute on the operator's own hardware.
 *
 * A rerank lane that fell back to in-process ONNX is *degraded*, not *cloud* —
 * counting it as a paid remote call is the mirror image of the bug where a
 * fallback was counted as a healthy local stage.
 */
const ON_BOX_SERVING = new Set([
  "ollama",
  "quenchforge",
  "onnx",
  "in-process",
  "sidecar",
  "local",
])

export function isOnBoxServing(provider: string | null | undefined): boolean {
  return ON_BOX_SERVING.has((provider ?? "").trim().toLowerCase())
}

export function providerLabel(provider: string | null | undefined): string {
  const p = (provider ?? "").trim().toLowerCase()
  if (!p) return "Unknown"
  return PROVIDER_LABEL[p] ?? provider!
}

export function laneLabel(lane: InferenceLaneName): string {
  return LANE_LABEL[lane]
}

/** The model to show for a lane: what served, else what was configured. */
export function laneModelLabel(lane: InferenceLane): string {
  return lane.servingModel || lane.model
}

/** True when the lane is configured against a model the deployment never pinned. */
export function laneModelUnset(lane: InferenceLane): boolean {
  const model = laneModelLabel(lane).trim().toLowerCase()
  return model === "" || model === "unset"
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

function readCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0
}

/** Narrow the untyped ``inference_routing`` payload into typed lanes. */
export function readInferenceLanes(raw: unknown): InferenceLane[] {
  if (raw == null || typeof raw !== "object") return []
  const src = raw as Record<string, unknown>
  const lanes: InferenceLane[] = []
  for (const lane of INFERENCE_LANES) {
    const block = src[lane]
    if (block == null || typeof block !== "object") continue
    const b = block as Record<string, unknown>
    lanes.push({
      lane,
      provider: readString(b.provider, "unknown"),
      model: readString(b.model),
      serving: readString(b.serving, "unknown"),
      servingModel: readString(b.serving_model),
      degraded: b.degraded === true,
      degradedDetail: readString(b.degraded_detail),
      fallbackCount: readCount(b.fallback_count),
    })
  }
  return lanes
}

export function degradedLanes(lanes: InferenceLane[]): InferenceLane[] {
  return lanes.filter((l) => l.degraded)
}

/** "reranking" / "LLM and reranking" — for a one-line verdict. */
export function degradedLaneSummary(lanes: InferenceLane[]): string {
  const names = degradedLanes(lanes).map((l) => LANE_LABEL[l.lane])
  if (names.length === 0) return ""
  if (names.length === 1) return names[0]
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
}

interface RowsProps {
  lanes: InferenceLane[]
  className?: string
}

/**
 * Per-lane rows: configured provider + model, what is actually serving, the
 * fallback count and the reason. Replaces the "Routing snapshot available"
 * placeholder that consumed this payload without showing any of it.
 */
export function InferenceLaneRows({ lanes, className }: RowsProps) {
  if (lanes.length === 0) {
    return (
      <p className="text-muted-foreground">
        No inference lane data reported by this backend.
      </p>
    )
  }
  return (
    <div className={cn("space-y-1", className)}>
      {lanes.map((lane) => {
        const model = laneModelLabel(lane)
        const unset = laneModelUnset(lane)
        return (
          <div key={lane.lane} data-testid={`inference-lane-${lane.lane}`}>
            <p
              className={cn(
                "font-medium",
                lane.degraded && "text-amber-700 dark:text-amber-400",
              )}
            >
              {LANE_LABEL[lane.lane]}: {providerLabel(lane.provider)}
              {model && !unset ? ` · ${model}` : ""}
            </p>
            <p className="text-muted-foreground">
              {lane.degraded
                ? `Serving ${providerLabel(lane.serving)} — degraded`
                : `Serving ${providerLabel(lane.serving)}`}
              {lane.fallbackCount > 0
                ? ` · ${lane.fallbackCount} fallback${lane.fallbackCount === 1 ? "" : "s"}`
                : ""}
            </p>
            {unset && lane.lane !== "sparse" && lane.lane !== "nli" && (
              <p className="text-amber-700 dark:text-amber-400">
                No model pinned for this lane.
              </p>
            )}
            {lane.degraded && lane.degradedDetail && (
              <p className="text-amber-700 dark:text-amber-400">{lane.degradedDetail}</p>
            )}
          </div>
        )
      })}
    </div>
  )
}
