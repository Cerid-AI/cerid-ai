// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas 4-lens system — design-system-v2 §3.6 + viz-spec §3.
// Each lens highlights a different facet of the knowledge graph;
// they stack via composeLenses() when multiple are active.

import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"
import type { Lens } from "./types"

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const CONTRADICTION_RED = "#FF6B6B"
const QUESTION_AMBER = "#E8C56A"
const DIMMED_GRAPHITE = "#3D4760"
const QUALITY_TEAL = "#5AECCB"

function dim(attrs: AtlasNodeAttributes): AtlasNodeAttributes {
  return {
    ...attrs,
    color: DIMMED_GRAPHITE,
    haloColor: DIMMED_GRAPHITE,
    pulseIntensity: Math.max(0.05, attrs.pulseIntensity * 0.35),
  }
}

function dimEdge(attrs: AtlasEdgeAttributes): AtlasEdgeAttributes {
  return {
    ...attrs,
    color: DIMMED_GRAPHITE,
    size: Math.max(0.2, attrs.size * 0.5),
  }
}

// ---------------------------------------------------------------------------
// Contradiction lens — contradicted edges (and the nodes they touch)
// pop in red; everything else fades into the background.
// ---------------------------------------------------------------------------

export const contradictionLens: Lens = {
  id: "contradiction",
  label: "Contradictions",
  description: "Highlights edges marked contradictory and the entities they touch.",
  legendColor: CONTRADICTION_RED,
  transformNode: (node, attrs, graph) => {
    let touchesContradiction = false
    graph.forEachEdge(node, (_e, eAttrs) => {
      if (eAttrs.contradiction) touchesContradiction = true
    })
    if (touchesContradiction) {
      return {
        ...attrs,
        haloColor: CONTRADICTION_RED,
        pulseIntensity: 1,
      }
    }
    return dim(attrs)
  },
  transformEdge: (_edge, attrs) => {
    if (attrs.contradiction) {
      return {
        ...attrs,
        color: CONTRADICTION_RED,
        size: Math.max(attrs.size, 2.5),
      }
    }
    return dimEdge(attrs)
  },
}

// ---------------------------------------------------------------------------
// Open-question lens — entities with low recency + low trust are likely
// unresolved questions; we surface them in amber and dim the rest.
// (v1 proxy. When the backend ships an explicit question_count or
// unresolved_claim_count, swap the predicate.)
// ---------------------------------------------------------------------------

export const openQuestionLens: Lens = {
  id: "open-question",
  label: "Open questions",
  description: "Surfaces entities with stale activity + unverified trust signals.",
  legendColor: QUESTION_AMBER,
  transformNode: (_node, attrs) => {
    const isOpenQuestion =
      attrs.recency_score < 0.4 &&
      (attrs.trust_state === "partial" || attrs.trust_state === "unverified" || attrs.trust_state === "unknown")
    if (isOpenQuestion) {
      return {
        ...attrs,
        haloColor: QUESTION_AMBER,
        pulseIntensity: 1,
      }
    }
    return dim(attrs)
  },
  transformEdge: (_edge, attrs) => dimEdge(attrs),
}

// ---------------------------------------------------------------------------
// Provenance lens — collapses node fill to a single accent per community
// and emphasizes the community structure. Edges within a community keep
// their normal color; cross-community edges fade. (v1 proxy for
// "where does each fact come from" — when source attribution lands as
// per-node provenance metadata, swap to that.)
// ---------------------------------------------------------------------------

export const provenanceLens: Lens = {
  id: "provenance",
  label: "Provenance",
  description: "Reveals community clustering — entities grouped by knowledge source.",
  legendColor: "#A87AE5",
  transformNode: (_node, attrs) => {
    // Brighter halo so community color stands out
    return {
      ...attrs,
      haloColor: attrs.color,
      pulseIntensity: Math.min(1, attrs.pulseIntensity * 1.3),
    }
  },
  transformEdge: (_edge, attrs, graph) => {
    const sourceCommunity = graph.getNodeAttribute(attrs.source, "community")
    const targetCommunity = graph.getNodeAttribute(attrs.target, "community")
    if (sourceCommunity === targetCommunity && sourceCommunity !== null) {
      return attrs
    }
    return dimEdge(attrs)
  },
}

// ---------------------------------------------------------------------------
// Quality lens — halo intensity maps to trust state. Verified entities
// blaze; contradicted entities ring red; unverified entities desaturate.
// ---------------------------------------------------------------------------

const TRUST_INTENSITY: Record<AtlasNodeAttributes["trust_state"], number> = {
  verified: 1.0,
  partial: 0.6,
  unverified: 0.35,
  contradicted: 1.0,  // bright but red
  unknown: 0.25,
}

export const qualityLens: Lens = {
  id: "quality",
  label: "Quality",
  description: "Maps halo brightness to verification confidence per entity.",
  legendColor: QUALITY_TEAL,
  transformNode: (_node, attrs) => {
    const intensity = TRUST_INTENSITY[attrs.trust_state] ?? 0.25
    let haloColor = attrs.haloColor
    if (attrs.trust_state === "contradicted") haloColor = CONTRADICTION_RED
    if (attrs.trust_state === "verified") haloColor = QUALITY_TEAL
    return {
      ...attrs,
      haloColor,
      pulseIntensity: intensity,
    }
  },
  transformEdge: (_edge, attrs) => attrs,
}

// ---------------------------------------------------------------------------
// Registry — single source of truth for lens lookups + ordered listings.
// ---------------------------------------------------------------------------

export const LENS_REGISTRY: Record<string, Lens> = {
  contradiction: contradictionLens,
  "open-question": openQuestionLens,
  provenance: provenanceLens,
  quality: qualityLens,
}

export const LENS_ORDER: Lens[] = [
  contradictionLens,
  openQuestionLens,
  provenanceLens,
  qualityLens,
]

export { composeLenses } from "./types"
export type { Lens, LensId } from "./types"
