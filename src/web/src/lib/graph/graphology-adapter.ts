// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Adapt /graph/neighborhood backend payload → graphology Graph instance
// ready for sigma.js rendering. Pure transformation; no I/O.
//
// Visual encoding decisions (per cerid-design-system-v2.md §3 + viz spec §2.2):
//   - Node radius = 8 + log(mention_count + 1) * 8 px  (cap at 48)
//   - Node fill color = community palette (12 OKLCH-derived clusters; fallback to graphite)
//   - Node halo color = trust state palette (verified/partial/unverified/contradicted/unknown)
//   - Edge thickness = 0.4 + log(weight + 1) * 0.6 px  (cap at 4)
//   - Edge color = relationship type palette

import Graph from "graphology"
import type {
  AtlasEdgeAttributes,
  AtlasNodeAttributes,
  GraphEdge,
  GraphNode,
  NeighborhoodResponse,
} from "@/lib/types/graph"

// ---------------------------------------------------------------------------
// Color palettes — keep in sync with src/web/src/index.css OKLCH tokens.
// These are static defaults; the shader-tokens generator (Phase A Day 5)
// will replace these with the canonical auto-generated values.
// ---------------------------------------------------------------------------

const COMMUNITY_PALETTE: string[] = [
  "#E5847A", "#E5A87A", "#E5C87A", "#D4AF37",
  "#C8E57A", "#A8E57A", "#7AE5C8", "#7AC8E5",
  "#7AA8E5", "#A87AE5", "#C87AE5", "#E57AC8",
]

const TRUST_HALO: Record<string, string> = {
  verified:     "#5AECCB",
  partial:      "#E8C56A",
  unverified:   "#D4AF37",
  contradicted: "#FF6B6B",
  unknown:      "#5C6680",
}

const EDGE_COLORS: Record<string, string> = {
  mentions:        "#7AC8E5",
  works_on:        "#D4AF37",
  discussed_with:  "#A8E57A",
  contradicts:     "#FF6B6B",
  temporal:        "#E8C56A",
}

const FALLBACK_COMMUNITY = "#5C6680"
const FALLBACK_HALO = TRUST_HALO.unknown
const FALLBACK_EDGE = EDGE_COLORS.mentions

// ---------------------------------------------------------------------------
// Sizing
// ---------------------------------------------------------------------------

const NODE_RADIUS_MIN = 8
const NODE_RADIUS_MAX = 48
const EDGE_WIDTH_MIN = 0.4
const EDGE_WIDTH_MAX = 4

function nodeSize(mentionCount: number): number {
  const raw = NODE_RADIUS_MIN + Math.log1p(Math.max(0, mentionCount)) * 8
  return Math.min(raw, NODE_RADIUS_MAX)
}

function edgeWidth(weight: number): number {
  const raw = EDGE_WIDTH_MIN + Math.log1p(Math.max(0, weight)) * 0.6
  return Math.min(raw, EDGE_WIDTH_MAX)
}

// ---------------------------------------------------------------------------
// Community color resolution — stable hash so the same community ID always
// gets the same color across page reloads, regardless of arrival order.
// ---------------------------------------------------------------------------

function hashStringToIndex(s: string, modulus: number): number {
  let hash = 0
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash) + s.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash) % modulus
}

function communityColor(communityId: string | null): string {
  if (!communityId) return FALLBACK_COMMUNITY
  return COMMUNITY_PALETTE[hashStringToIndex(communityId, COMMUNITY_PALETTE.length)]
}

function haloColor(trustState: string): string {
  return TRUST_HALO[trustState] ?? FALLBACK_HALO
}

function edgeColor(edgeType: string, contradiction: boolean): string {
  if (contradiction) return TRUST_HALO.contradicted
  return EDGE_COLORS[edgeType] ?? FALLBACK_EDGE
}

// ---------------------------------------------------------------------------
// Label truncation — graph labels are tight; truncate names > 28 chars
// with ellipsis. Hover tooltip shows the full name.
// ---------------------------------------------------------------------------

function truncateLabel(name: string): string {
  const MAX = 28
  if (name.length <= MAX) return name
  return name.slice(0, MAX - 1) + "…"
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

function pulseIntensity(node: GraphNode): number {
  // Base intensity from recency. Floor at 0.25 so even stale entities
  // still show a faint halo (otherwise unknown/old nodes look broken).
  const base = Math.max(0.25, Math.min(1, node.recency_score))
  // Focused entities glow 40% brighter, clamped.
  return node.focused ? Math.min(1, base * 1.4) : base
}

function nodeAttrs(node: GraphNode): AtlasNodeAttributes {
  return {
    ...node,
    x: 0,  // populated by force-atlas2 layout
    y: 0,
    size: nodeSize(node.mention_count),
    label: truncateLabel(node.name),
    color: communityColor(node.community),
    haloColor: haloColor(node.trust_state),
    pulseIntensity: pulseIntensity(node),
    type: "haloed",
  }
}

function edgeAttrs(edge: GraphEdge): AtlasEdgeAttributes {
  return {
    ...edge,
    size: edgeWidth(edge.weight),
    color: edgeColor(edge.type, edge.contradiction),
  }
}

/**
 * Build a graphology Graph from the neighborhood API response.
 *
 * Idempotent: passing the same response twice yields graphs with the
 * same nodes/edges in the same order. Nodes are dropped if their ID is
 * missing or empty. Edges are dropped if either endpoint is missing.
 */
export function adaptNeighborhood(response: NeighborhoodResponse): Graph<
  AtlasNodeAttributes,
  AtlasEdgeAttributes
> {
  const graph = new Graph<AtlasNodeAttributes, AtlasEdgeAttributes>({
    type: "mixed",
    multi: false,
    allowSelfLoops: false,
  })

  for (const node of response.nodes) {
    if (!node.id) continue
    if (graph.hasNode(node.id)) continue  // defensive — API should dedupe but we guard
    graph.addNode(node.id, nodeAttrs(node))
  }

  for (const edge of response.edges) {
    if (!edge.source || !edge.target) continue
    if (edge.source === edge.target) continue
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue
    const edgeKey = `${edge.source}::${edge.target}::${edge.type}`
    if (graph.hasEdge(edgeKey)) continue
    graph.addEdgeWithKey(edgeKey, edge.source, edge.target, edgeAttrs(edge))
  }

  return graph
}

// Export internals for unit testing
export const __TESTING__ = {
  nodeSize,
  edgeWidth,
  communityColor,
  haloColor,
  edgeColor,
  truncateLabel,
  hashStringToIndex,
  pulseIntensity,
}
