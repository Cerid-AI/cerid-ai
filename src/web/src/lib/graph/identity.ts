// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Shared visual identity pipeline for Atlas and Wiki MiniGraph.
// Single source of truth for community/trust color resolution.
// Pure module — no WebGL, no canvas, safe in unit tests.
//
// Node/edge program factories (WebGL) live in atlas-programs.ts.

import type Graph from "graphology"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"

// Re-export from community-layer so Atlas/Wiki import one canonical location.
export {
  resolveMapTokens,
  type MapTokens,
} from "@/components/subjects/constellation/map/community-layer"

import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"

// ---------------------------------------------------------------------------
// communitySlot — byte-identical to community-layer and strata-layout.
// ---------------------------------------------------------------------------

export function communitySlot(id: string): number {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash) % 8
}

// ---------------------------------------------------------------------------
// Token-resolved color helpers (pure — no WebGL)
// ---------------------------------------------------------------------------

export function clusterColor(tokens: MapTokens, communityId: string | null | undefined): string {
  if (!communityId) return tokens.clusterOther
  const slot = communitySlot(communityId)
  return tokens.clusters[slot] ?? tokens.clusterOther
}

export function trustColor(tokens: MapTokens, trustState: string): string {
  switch (trustState) {
    case "verified":     return tokens.trustVerified
    case "partial":      return tokens.trustPartial
    case "unverified":   return tokens.trustUnverified
    case "contradicted": return tokens.trustUnverified // lens adds red accent; base ring = unverified
    default:             return tokens.dim
  }
}

// ---------------------------------------------------------------------------
// Node sizing — sqrt ramp (~6px floor, ~18px cap) per design doc
// ---------------------------------------------------------------------------

const NODE_SIZE_MIN = 6
const NODE_SIZE_MAX = 18

export function nodeSize(mentionCount: number): number {
  const safe = Math.max(0, mentionCount)
  const raw = NODE_SIZE_MIN + Math.sqrt(safe) * 1.2
  return Math.min(raw, NODE_SIZE_MAX)
}

// ---------------------------------------------------------------------------
// Parallel edge fanning — call after graph is built to assign curvatures.
// Uses a lazy dynamic import so the @sigma/edge-curve WebGL bundle is NOT
// imported at module parse time (keeps pure unit tests from blowing up).
// ---------------------------------------------------------------------------

interface EdgeWithCurve {
  parallelIndex?: number
  parallelMinIndex?: number
  parallelMaxIndex?: number
  curvature?: number
}

export async function applyParallelEdgeCurvature(
  graph: Graph<AtlasNodeAttributes, AtlasEdgeAttributes>
): Promise<void> {
  // Lazy import keeps @sigma/edge-curve (which pulls sigma/rendering + WebGL)
  // out of the module parse graph so pure-unit tests don't blow up in jsdom.
  const { indexParallelEdgesIndex } = await import("@sigma/edge-curve")
  indexParallelEdgesIndex(graph, {
    edgeIndexAttribute: "parallelIndex",
    edgeMinIndexAttribute: "parallelMinIndex",
    edgeMaxIndexAttribute: "parallelMaxIndex",
  })
  graph.forEachEdge((_key, attrs) => {
    const e = attrs as AtlasEdgeAttributes & EdgeWithCurve
    if (typeof e.parallelIndex === "number" && typeof e.parallelMaxIndex === "number" && e.parallelMaxIndex > 0) {
      e.curvature = (e.parallelIndex / e.parallelMaxIndex - 0.5) * 0.8
    }
  })
}
