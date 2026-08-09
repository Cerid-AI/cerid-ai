// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
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
// domainSlot — stable hash into one of 12 domain palette slots.
//
// Algorithm shape is byte-consistent with communitySlot: accumulate per char
// with (h<<5)-h + charCode, |=0 after each step.  After accumulation we apply
// an avalanche finalization step folding in DOMAIN_HASH_SALT (796) — the
// single documented seed constant required to guarantee the 12 canonical
// taxonomy domain names ("coding", "finance", "projects", "personal",
// "general", "conversations", "notes", "mail", "messages", "meetings",
// "inbox", "digests") each map to a distinct slot.  Without the salt the
// base polynomial hash % 12 has four 3-way collisions for these names.
// The salt value (796) was found by exhaustive search; it is the smallest
// positive integer that produces a bijection for the canonical 12.
//
// Slot assignments for the canonical 12 (verified in identity.test.ts):
//   projects=0  meetings=1  inbox=2  finance=3  messages=4
//   notes=5     general=6   coding=7  conversations=8  mail=9
//   digests=10  personal=11
// ---------------------------------------------------------------------------

const DOMAIN_HASH_SALT = 796

export function domainSlot(domain: string): number {
  let hash = 0
  for (let i = 0; i < domain.length; i++) {
    hash = ((hash << 5) - hash) + domain.charCodeAt(i)
    hash |= 0
  }
  // Avalanche finalization — fold in the documented seed constant so the
  // canonical 12 built-in domain names are collision-free at % 12.
  hash ^= hash >>> 16
  hash = Math.imul(hash, DOMAIN_HASH_SALT)
  hash ^= hash >>> 16
  return (hash >>> 0) % 12
}

export function domainColor(tokens: MapTokens, domain: string | null | undefined): string {
  if (!domain) return tokens.domainOther
  return tokens.domains[domainSlot(domain)] ?? tokens.domainOther
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
