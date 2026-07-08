// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// TypeScript types mirroring the /graph/* backend response shapes.
// Contract: must match the Pydantic models in src/mcp/app/routers/graph.py.
// The test_graph_router::test_neighborhood_response_shape_matches_atlas_contract
// guards the backend side; frontend side is enforced by these types + the
// adapter unit tests.

/** Visual node payload from /graph/neighborhood */
export interface GraphNode {
  id: string
  name: string
  /** Entity type: Person / Project / Topic / Place / Organization / Document / Event / Claim */
  type: string
  /** Leiden community ID, or null if entity not yet assigned a cluster */
  community: string | null
  /** Total mentions across the corpus (log-scaled to node radius in Atlas) */
  mention_count: number
  /** verified | partial | unverified | contradicted | unknown */
  trust_state: "verified" | "partial" | "unverified" | "contradicted" | "unknown"
  /** 0..1; recent mentions push toward 1; drives halo pulse rate */
  recency_score: number
  /** True for the focal entity of the current view */
  focused: boolean
  /** Primary domain derived by DeriveDomainsJob; null until first derivation run */
  primary_domain?: string | null
}

/** Visual edge payload from /graph/neighborhood */
export interface GraphEdge {
  source: string
  target: string
  /** mentions | works_on | discussed_with | contradicts | temporal */
  type: string
  /** log(co_mentions+1) — feeds Atlas edge thickness */
  weight: number
  /** attested | inferred — feeds Atlas edge stroke style */
  attestation: "attested" | "inferred"
  /** True when the relationship is marked contradictory in Cerid's analysis */
  contradiction: boolean
}

/** /graph/neighborhood response envelope */
export interface NeighborhoodResponse {
  focal_entity: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** True if the result hit MAX_DEGREE or hop ceiling and was truncated */
  truncated: boolean
  /** True if the response was served from Redis LRU (debug/observability signal) */
  cached: boolean
  /** Number of isolated (degree-0) entities excluded when include_isolated=false */
  isolated_count: number
}

/** /graph/health response */
export interface GraphHealth {
  neo4j_available: boolean
  cache_ttl_seconds: number
  max_node_degree: number
  max_hops: number
  visualization_enabled: boolean
}

/**
 * Atlas-internal node attributes — what we set on the graphology graph
 * for sigma.js to render. Extends backend GraphNode with renderer-only
 * concerns (x/y coords from layout, size from log scale, label from name).
 */
export interface AtlasNodeAttributes extends GraphNode {
  /** Layout x coordinate (force-atlas2 output) */
  x: number
  /** Layout y coordinate */
  y: number
  /** Node radius after log-scaling mention_count (px) */
  size: number
  /** Display label (mirrors name with truncation for long entity names) */
  label: string
  /** Fill color hex (community cluster from tokens pipeline) */
  color: string
  /** Border/ring color hex (trust state from tokens pipeline; consumed by NodeBorderProgram) */
  borderColor?: string
  /** Halo color hex (trust state palette; kept for NodeHaloProgram compat) */
  haloColor: string
  /**
   * Halo brightness scalar in [0, 1]. Derived from recency_score (×1.0)
   * and focused state (×1.4, clamped). Consumed by NodeHaloProgram.
   * Visual encoding: recent + focused entities glow brighter, stale ones
   * fade toward graphite. v1 is static — animated pulse arrives later.
   */
  pulseIntensity: number
  /**
   * Sigma node program key — routes the node through a registered
   * program in nodeProgramClasses. "bordered" uses the Meridian
   * NodeBorderProgram (trust ring + community fill compound).
   */
  type: "haloed" | "bordered"
  /**
   * The API entity type (Person / Project / Topic / …). GraphNode.type is
   * shadowed by the sigma program key above, so the adapter re-homes it
   * here — the type-chip toolbar and reducers read THIS field.
   */
  entityType: string
  /**
   * Sigma built-in node attribute toggled by hover handlers (`enterNode`
   * / `leaveNode`). When true, sigma renders the node above its peers
   * and applies the default highlight palette. Declared optional so
   * callers don't have to seed it at graph build time.
   */
  highlighted?: boolean
  /**
   * Sigma built-in: when true, sigma always renders the label for this node
   * regardless of the label density setting. Used for the focal node.
   */
  forceLabel?: boolean
  /**
   * Growth tween 0→1 for nodes entering during an A5 ego migration; the
   * node reducer fades alpha by it. Undefined (or 1) = fully arrived.
   */
  spawnProgress?: number
}

/** Atlas-internal edge attributes */
export interface AtlasEdgeAttributes extends GraphEdge {
  /** Edge thickness in px after log-scaling weight */
  size: number
  /** Edge color hex (relationship type palette per design-system-v2 §3.4) */
  color: string
  /** Sigma built-in: set by the zoom-LOD edge reducer below the tier floor. */
  hidden?: boolean
}
