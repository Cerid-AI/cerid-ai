// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure composed reducer chain for Atlas (Living-Map A1): lens compose →
// type-chip dim → hover/pin spotlight fade. Extracted from Atlas.tsx so
// the composition is unit-testable without sigma/WebGL. The reducers are
// ALWAYS installed; per-frame state (spotlight) is read through the
// injected controller, so hover never reinstalls reducers.

import type { AtlasNodeAttributes, AtlasEdgeAttributes } from "@/lib/types/graph"
import { focusNodeAlpha, focusNodeSize } from "@/lib/graph/interactions/focus-spotlight"
import { lodEdgeAlpha } from "@/components/subjects/constellation/map/semantic-zoom"

export type LodTier = "overview" | "mid" | "detail"

/** Minimum rendered node size under dim/fade (hit-target floor). */
const NODE_SIZE_FLOOR = 3

/** Non-focus edges fade harder than nodes so the neighborhood reads clearly. */
const EDGE_FADE_SCALE = 0.6

export interface SpotlightReader {
  neighbors(): Set<string> | null
  progress(): number
}

export interface AtlasReducerTokens {
  clusterOther: string
  edge: string
}

/**
 * Suffix (or replace) the alpha byte of a #rrggbb(aa) color; non-hex
 * strings pass through untouched. Output is normalized to lowercase.
 */
export function hexWithAlpha(color: string, alpha: number): string {
  if (!color.startsWith("#")) return color
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, "0")
  if (color.length === 7) return (color + a).toLowerCase()
  if (color.length === 9) return (color.slice(0, 7) + a).toLowerCase()
  return color
}

export interface AtlasNodeReducerDeps {
  lensNodeReducer?: (node: string, attrs: AtlasNodeAttributes) => AtlasNodeAttributes
  typeChips: Set<string>
  tokens: AtlasReducerTokens
  spotlight: SpotlightReader
}

export function buildAtlasNodeReducer(
  deps: AtlasNodeReducerDeps,
): (node: string, attrs: AtlasNodeAttributes) => AtlasNodeAttributes {
  const { lensNodeReducer, typeChips, tokens, spotlight } = deps
  return (node, attrs) => {
    let reduced: AtlasNodeAttributes = lensNodeReducer ? lensNodeReducer(node, attrs) : { ...attrs }
    // Fade contributions multiply (spotlight × spawn growth) into one
    // hue-preserving alpha application at the end.
    let alphaMul = 1

    // Type chips ghost filtered-out entities (dim, not hide — context stays).
    if (typeChips.size > 0 && !typeChips.has(reduced.entityType)) {
      reduced = {
        ...reduced,
        color: tokens.clusterOther,
        size: Math.max(reduced.size * 0.5, NODE_SIZE_FLOOR),
      }
    }

    // Hover/pin spotlight: non-neighbors EASE out — hue-preserving alpha
    // fade + gentle shrink driven by the controller's eased progress.
    const focusNeighbors = spotlight.neighbors()
    if (focusNeighbors && !focusNeighbors.has(node)) {
      const p = spotlight.progress()
      alphaMul *= focusNodeAlpha(1, p)
      reduced = { ...reduced, label: "", size: focusNodeSize(reduced.size, p, NODE_SIZE_FLOOR) }
    }

    // Entering nodes (A5 migration) fade in with their growth tween.
    const spawn = reduced.spawnProgress
    if (typeof spawn === "number" && spawn < 1) {
      alphaMul *= Math.max(0, spawn)
    }

    if (alphaMul < 1) {
      reduced = { ...reduced, color: hexWithAlpha(reduced.color, alphaMul) }
    }
    return reduced
  }
}

export interface AtlasEdgeReducerDeps {
  lensEdgeReducer?: (edge: string, attrs: AtlasEdgeAttributes) => AtlasEdgeAttributes
  typeChips: Set<string>
  tokens: AtlasReducerTokens
  spotlight: SpotlightReader
  graph: {
    source(edge: string): string
    target(edge: string): string
    /** Endpoint attr read for the A5 spawn/exit edge fade (spawnProgress). */
    getNodeAttribute(node: string, attr: string): unknown
  }
  /** Zoom-LOD tier (A2): thin edges fade then hide as the camera pulls back. */
  getLodTier?: () => LodTier
}

export function buildAtlasEdgeReducer(
  deps: AtlasEdgeReducerDeps,
): (edge: string, attrs: AtlasEdgeAttributes) => AtlasEdgeAttributes {
  const { lensEdgeReducer, tokens, spotlight, graph, getLodTier } = deps
  const spawnOf = (node: string): number => {
    const v = graph.getNodeAttribute(node, "spawnProgress")
    return typeof v === "number" ? v : 1
  }
  return (edge, attrs) => {
    let reduced: AtlasEdgeAttributes = lensEdgeReducer ? lensEdgeReducer(edge, attrs) : { ...attrs }
    // Fade contributions multiply (migration tween × spotlight/LOD) into one
    // hue-preserving alpha application at the end — mirrors the node reducer.
    let alphaMul = 1

    // A5 migration: an edge tracks its weaker endpoint's spawn/exit tween
    // (enter 0→1, exit 1→0) so exiting nodes keep visibly-attached edges
    // and entering nodes never show pre-wired full-strength edges mid-morph.
    const growth = Math.min(spawnOf(graph.source(edge)), spawnOf(graph.target(edge)))
    if (growth < 1) alphaMul *= Math.max(0, growth)

    const focusNeighbors = spotlight.neighbors()
    if (focusNeighbors) {
      // Neighborhood edges stay at full strength (and ignore the LOD floor);
      // outside edges fade with the same eased progress as nodes.
      if (!(focusNeighbors.has(graph.source(edge)) && focusNeighbors.has(graph.target(edge)))) {
        alphaMul *= focusNodeAlpha(1, spotlight.progress()) * EDGE_FADE_SCALE
      }
    } else {
      // No focus: zoom-LOD fade — thin edges lose alpha across the fade band
      // and drop out entirely below the tier floor, so pulled-back views read
      // as structure instead of hairball.
      const tier = getLodTier?.() ?? "detail"
      if (tier !== "detail") {
        const lodAlpha = lodEdgeAlpha(tier, reduced.size)
        if (lodAlpha <= 0) return { ...reduced, hidden: true }
        alphaMul *= lodAlpha
      }
    }

    if (alphaMul < 1) {
      reduced = { ...reduced, color: hexWithAlpha(reduced.color || tokens.edge, alphaMul) }
    }
    return reduced
  }
}
