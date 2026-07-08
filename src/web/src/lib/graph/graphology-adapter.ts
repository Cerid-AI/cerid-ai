// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Adapt /graph/neighborhood backend payload → graphology Graph instance
// ready for sigma.js rendering. Pure transformation; no I/O.
//
// Visual encoding decisions (Meridian identity pipeline):
//   - Node fill  = clusterColor(tokens, community) from identity.ts
//   - Node border = trustColor(tokens, trust_state) from identity.ts
//   - Node size  = nodeSize(mention_count) sqrt ramp 6–18px
//   - Edge color = tokens.edge (neutral); contradiction flag set for lens
//   - Edge width = log-sqrt ramp, 0.4–4px

import Graph from "graphology"
import type {
  AtlasEdgeAttributes,
  AtlasNodeAttributes,
  GraphEdge,
  GraphNode,
  NeighborhoodResponse,
} from "@/lib/types/graph"
import {
  clusterColor,
  trustColor,
  nodeSize as nodeSizeFromIdentity,
} from "./identity"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"

// ---------------------------------------------------------------------------
// Sizing
// ---------------------------------------------------------------------------

const EDGE_WIDTH_MIN = 0.4
const EDGE_WIDTH_MAX = 4

function edgeWidth(weight: number): number {
  const raw = EDGE_WIDTH_MIN + Math.log1p(Math.max(0, weight)) * 0.6
  return Math.min(raw, EDGE_WIDTH_MAX)
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
// Adapter internals
// ---------------------------------------------------------------------------

function pulseIntensity(node: GraphNode): number {
  const base = Math.max(0.25, Math.min(1, node.recency_score))
  return node.focused ? Math.min(1, base * 1.4) : base
}

function nodeAttrs(node: GraphNode, tokens: MapTokens): AtlasNodeAttributes {
  return {
    ...node,
    x: 0,  // populated by force-atlas2 layout
    y: 0,
    size: nodeSizeFromIdentity(node.mention_count),
    label: truncateLabel(node.name),
    color: clusterColor(tokens, node.community),
    // borderColor is read by the NodeBorderProgram trust ring
    borderColor: trustColor(tokens, node.trust_state),
    haloColor: trustColor(tokens, node.trust_state),  // kept for NodeHaloProgram fallback
    pulseIntensity: pulseIntensity(node),
    // node.type (the API entity type) is shadowed by the sigma program key —
    // preserve it for the type-chip toolbar + reducers.
    entityType: node.type,
    type: "bordered",
  }
}

function edgeAttrs(edge: GraphEdge, tokens: MapTokens): AtlasEdgeAttributes {
  return {
    ...edge,
    size: edgeWidth(edge.weight),
    // All edges neutral unless the Contradictions lens activates.
    // Contradiction flag is preserved via edge.contradiction for reducer use.
    color: tokens.edge,
    type: "curved",
  }
}

/**
 * Build a graphology Graph from the neighborhood API response.
 * Colors are resolved from the provided MapTokens so they stay in sync
 * with the live theme. Call with fresh tokens on theme change.
 *
 * Idempotent: passing the same response twice yields graphs with the
 * same nodes/edges in the same order. Nodes are dropped if their ID is
 * missing or empty. Edges are dropped if either endpoint is missing.
 */
export function adaptNeighborhood(
  response: NeighborhoodResponse,
  tokens: MapTokens,
): Graph<AtlasNodeAttributes, AtlasEdgeAttributes> {
  const graph = new Graph<AtlasNodeAttributes, AtlasEdgeAttributes>({
    type: "mixed",
    multi: false,
    allowSelfLoops: false,
  })

  for (const node of response.nodes) {
    if (!node.id) continue
    if (graph.hasNode(node.id)) continue  // defensive — API should dedupe but we guard
    graph.addNode(node.id, nodeAttrs(node, tokens))
  }

  for (const edge of response.edges) {
    if (!edge.source || !edge.target) continue
    if (edge.source === edge.target) continue
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue
    const edgeKey = `${edge.source}::${edge.target}::${edge.type}`
    if (graph.hasEdge(edgeKey)) continue
    graph.addEdgeWithKey(edgeKey, edge.source, edge.target, edgeAttrs(edge, tokens))
  }

  return graph
}

/**
 * Re-apply token-derived colors to an existing graph without a full rebuild.
 * Called when the theme changes to push new hex values into sigma via
 * graph.setNodeAttribute / setEdgeAttribute + sigma.refresh().
 */
export function recolorGraph(
  graph: Graph<AtlasNodeAttributes, AtlasEdgeAttributes>,
  tokens: MapTokens,
): void {
  graph.forEachNode((id, attrs) => {
    const fill = clusterColor(tokens, attrs.community)
    const border = trustColor(tokens, attrs.trust_state)
    graph.setNodeAttribute(id, "color", fill)
    graph.setNodeAttribute(id, "borderColor", border)
    graph.setNodeAttribute(id, "haloColor", border)
  })
  graph.forEachEdge((key) => {
    graph.setEdgeAttribute(key, "color", tokens.edge)
  })
}

// Export internals for unit testing
export const __TESTING__ = {
  nodeSize: nodeSizeFromIdentity,
  edgeWidth,
  truncateLabel,
  pulseIntensity,
}
