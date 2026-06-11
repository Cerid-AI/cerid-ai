// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas 4-lens system — design-system-v2 §3.6 + viz-spec §3.
// Each lens highlights a different facet of the knowledge graph;
// they stack via composeLenses() when multiple are active.
//
// Lens accent hexes are sourced from resolved MapTokens passed at
// composition time so they remain theme-aware.

import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"
import { composeLenses as _composeLenses } from "./types"
import type { Lens, LensId } from "./types"
import { domainColor } from "@/lib/graph/identity"

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function dim(attrs: AtlasNodeAttributes, dimColor: string): AtlasNodeAttributes {
  return {
    ...attrs,
    color: dimColor,
    haloColor: dimColor,
    pulseIntensity: Math.max(0.05, attrs.pulseIntensity * 0.35),
  }
}

function dimEdge(attrs: AtlasEdgeAttributes, dimColor: string): AtlasEdgeAttributes {
  return {
    ...attrs,
    color: dimColor,
    size: Math.max(0.2, attrs.size * 0.5),
  }
}

// ---------------------------------------------------------------------------
// Lens factories — take tokens so accent colors are theme-resolved.
// ---------------------------------------------------------------------------

export function makeContradictionLens(tokens: MapTokens): Lens {
  const contradictionColor = tokens.trustUnverified  // red band for contradiction
  const dimColor = tokens.dim
  return {
    id: "contradiction",
    label: "Contradictions",
    description: "Highlights edges marked contradictory and the entities they touch.",
    legendColor: contradictionColor,
    transformNode: (node, attrs, graph) => {
      let touchesContradiction = false
      graph.forEachEdge(node, (_e, eAttrs) => {
        if (eAttrs.contradiction) touchesContradiction = true
      })
      if (touchesContradiction) {
        return { ...attrs, haloColor: contradictionColor, pulseIntensity: 1 }
      }
      return dim(attrs, dimColor)
    },
    transformEdge: (_edge, attrs) => {
      if (attrs.contradiction) {
        return { ...attrs, color: contradictionColor, size: Math.max(attrs.size, 2.5) }
      }
      return dimEdge(attrs, dimColor)
    },
  }
}

export function makeOpenQuestionLens(tokens: MapTokens): Lens {
  const questionColor = tokens.trustPartial  // amber band for open questions
  const dimColor = tokens.dim
  return {
    id: "open-question",
    label: "Open questions",
    description: "Surfaces entities with stale activity + unverified trust signals.",
    legendColor: questionColor,
    transformNode: (_node, attrs) => {
      const isOpenQuestion =
        attrs.recency_score < 0.4 &&
        (attrs.trust_state === "partial" || attrs.trust_state === "unverified" || attrs.trust_state === "unknown")
      if (isOpenQuestion) {
        return { ...attrs, haloColor: questionColor, pulseIntensity: 1 }
      }
      return dim(attrs, dimColor)
    },
    transformEdge: (_edge, attrs) => dimEdge(attrs, dimColor),
  }
}

export function makeProvenanceLens(tokens: MapTokens): Lens {
  const dimColor = tokens.dim
  return {
    id: "provenance",
    label: "Provenance",
    description: "Reveals community clustering — entities grouped by knowledge source.",
    legendColor: tokens.clusters[4] ?? tokens.clusterOther,  // mauve slot as legend
    transformNode: (_node, attrs) => ({
      ...attrs,
      haloColor: attrs.color,  // community hue on ring
      pulseIntensity: Math.min(1, attrs.pulseIntensity * 1.3),
    }),
    transformEdge: (_edge, attrs, graph) => {
      const sourceCommunity = graph.getNodeAttribute(attrs.source, "community")
      const targetCommunity = graph.getNodeAttribute(attrs.target, "community")
      if (sourceCommunity === targetCommunity && sourceCommunity !== null) return attrs
      return dimEdge(attrs, dimColor)
    },
  }
}

export function makeQualityLens(tokens: MapTokens): Lens {
  const TRUST_INTENSITY: Record<AtlasNodeAttributes["trust_state"], number> = {
    verified:    1.0,
    partial:     0.6,
    unverified:  0.35,
    contradicted: 1.0,  // bright but red
    unknown:     0.25,
  }
  const contradictionColor = tokens.trustUnverified
  const verifiedColor = tokens.trustVerified
  return {
    id: "quality",
    label: "Quality",
    description: "Maps halo brightness to verification confidence per entity.",
    legendColor: verifiedColor,
    transformNode: (_node, attrs) => {
      const intensity = TRUST_INTENSITY[attrs.trust_state] ?? 0.25
      let haloColor = attrs.haloColor
      if (attrs.trust_state === "contradicted") haloColor = contradictionColor
      if (attrs.trust_state === "verified") haloColor = verifiedColor
      return { ...attrs, haloColor, pulseIntensity: intensity }
    },
    transformEdge: (_edge, attrs) => attrs,
  }
}

export function makeDomainLens(tokens: MapTokens): Lens {
  const dimColor = tokens.dim
  return {
    id: "domain",
    label: "Domains",
    description: "Colors nodes by primary knowledge domain; dims cross-domain edges.",
    legendColor: tokens.domains[7] ?? tokens.domainOther,  // coding slot as swatch
    transformNode: (_node, attrs) => {
      const color = domainColor(tokens, (attrs as AtlasNodeAttributes & { primary_domain?: string | null }).primary_domain)
      return { ...attrs, color, haloColor: color }
    },
    transformEdge: (_edge, attrs, graph) => {
      const srcDomain = (graph.getNodeAttribute(attrs.source, "primary_domain") as string | null | undefined)
      const tgtDomain = (graph.getNodeAttribute(attrs.target, "primary_domain") as string | null | undefined)
      // Dim cross-domain edges so intra-domain structure pops
      if (srcDomain && tgtDomain && srcDomain === tgtDomain) return attrs
      return dimEdge(attrs, dimColor)
    },
  }
}

// ---------------------------------------------------------------------------
// Default static instances (tokens-free, for use in tests and lens panel)
// The legend colors are approximate token approximations; when composeLenses
// is called with tokens, use the token-resolved factories above.
// ---------------------------------------------------------------------------

const CONTRADICTION_LEGEND = "#E05555" // drift-allowed: legend-only fallback; overridden by makeContradictionLens(tokens) at runtime
const QUESTION_LEGEND =      "#C89A35" // drift-allowed: legend-only fallback; overridden by makeOpenQuestionLens(tokens) at runtime
const PROVENANCE_LEGEND =    "#7A6BB5" // drift-allowed: legend-only fallback; overridden by makeProvenanceLens(tokens) at runtime
const QUALITY_LEGEND =       "#4488AA" // drift-allowed: legend-only fallback; overridden by makeQualityLens(tokens) at runtime
const DOMAIN_LEGEND =        "#3E7F6D" // drift-allowed: legend-only fallback; overridden by makeDomainLens(tokens) at runtime

export const contradictionLens: Lens = {
  id: "contradiction",
  label: "Contradictions",
  description: "Highlights edges marked contradictory and the entities they touch.",
  legendColor: CONTRADICTION_LEGEND,
  transformNode: (node, attrs, graph) => {
    let touchesContradiction = false
    graph.forEachEdge(node, (_e, eAttrs) => { if (eAttrs.contradiction) touchesContradiction = true })
    if (touchesContradiction) return { ...attrs, haloColor: CONTRADICTION_LEGEND, pulseIntensity: 1 }
    return { ...attrs, color: attrs.color, haloColor: attrs.color, pulseIntensity: Math.max(0.05, attrs.pulseIntensity * 0.35) }
  },
  transformEdge: (_edge, attrs) => {
    if (attrs.contradiction) return { ...attrs, color: CONTRADICTION_LEGEND, size: Math.max(attrs.size, 2.5) }
    return { ...attrs, size: Math.max(0.2, attrs.size * 0.5) }
  },
}

export const openQuestionLens: Lens = {
  id: "open-question",
  label: "Open questions",
  description: "Surfaces entities with stale activity + unverified trust signals.",
  legendColor: QUESTION_LEGEND,
  transformNode: (_node, attrs) => {
    const isOpenQuestion = attrs.recency_score < 0.4 &&
      (attrs.trust_state === "partial" || attrs.trust_state === "unverified" || attrs.trust_state === "unknown")
    if (isOpenQuestion) return { ...attrs, haloColor: QUESTION_LEGEND, pulseIntensity: 1 }
    return { ...attrs, color: attrs.color, haloColor: attrs.color, pulseIntensity: Math.max(0.05, attrs.pulseIntensity * 0.35) }
  },
  transformEdge: (_edge, attrs) => ({ ...attrs, size: Math.max(0.2, attrs.size * 0.5) }),
}

export const provenanceLens: Lens = {
  id: "provenance",
  label: "Provenance",
  description: "Reveals community clustering — entities grouped by knowledge source.",
  legendColor: PROVENANCE_LEGEND,
  transformNode: (_node, attrs) => ({
    ...attrs,
    haloColor: attrs.color,
    pulseIntensity: Math.min(1, attrs.pulseIntensity * 1.3),
  }),
  transformEdge: (_edge, attrs, graph) => {
    const src = graph.getNodeAttribute(attrs.source, "community")
    const tgt = graph.getNodeAttribute(attrs.target, "community")
    if (src === tgt && src !== null) return attrs
    return { ...attrs, size: Math.max(0.2, attrs.size * 0.5) }
  },
}

const TRUST_INTENSITY_STATIC: Record<AtlasNodeAttributes["trust_state"], number> = {
  verified: 1.0, partial: 0.6, unverified: 0.35, contradicted: 1.0, unknown: 0.25,
}

export const qualityLens: Lens = {
  id: "quality",
  label: "Quality",
  description: "Maps halo brightness to verification confidence per entity.",
  legendColor: QUALITY_LEGEND,
  transformNode: (_node, attrs) => {
    const intensity = TRUST_INTENSITY_STATIC[attrs.trust_state] ?? 0.25
    let haloColor = attrs.haloColor
    if (attrs.trust_state === "contradicted") haloColor = CONTRADICTION_LEGEND
    if (attrs.trust_state === "verified") haloColor = QUALITY_LEGEND
    return { ...attrs, haloColor, pulseIntensity: intensity }
  },
  transformEdge: (_edge, attrs) => attrs,
}

export const domainLens: Lens = {
  id: "domain",
  label: "Domains",
  description: "Colors nodes by primary knowledge domain; dims cross-domain edges.",
  legendColor: DOMAIN_LEGEND,
  transformNode: (_node, attrs) => {
    // Static fallback: no tokens available, so colors are unchanged.
    // The token-resolved makeDomainLens(tokens) is used at runtime.
    return attrs
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
  domain: domainLens,
}

export const LENS_ORDER: Lens[] = [
  contradictionLens,
  openQuestionLens,
  provenanceLens,
  qualityLens,
  domainLens,
]

/**
 * Compose lenses with token-resolved accent colors.
 * Prefer this over composeLenses when tokens are available.
 */
export function composeLensesWithTokens(
  lensIds: LensId[],
  tokens: MapTokens,
  graph: Parameters<typeof _composeLenses>[1],
) {
  const resolved = lensIds.map((id) => {
    switch (id) {
      case "contradiction":  return makeContradictionLens(tokens)
      case "open-question":  return makeOpenQuestionLens(tokens)
      case "provenance":     return makeProvenanceLens(tokens)
      case "quality":        return makeQualityLens(tokens)
      case "domain":         return makeDomainLens(tokens)
    }
  })
  return _composeLenses(resolved, graph)
}

export { composeLenses } from "./types"
export type { Lens, LensId } from "./types"
