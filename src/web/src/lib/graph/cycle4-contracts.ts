// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// FROZEN cross-agent contract for Cycle 4 — Atlas STRATA.
//
// This file is READ-ONLY after first commit. Additions allowed (mark
// with "// added:" + reason). No modifications to existing definitions.
//
// Binding artifacts:
//   tasks/2026-06-11-cycle4-concept-strata.md  (winning design)
//   tasks/2026-06-11-cycle4-verdict.json        (amendments A1–A6)
//   tasks/2026-06-11-cycle4-grounding-hierarchy.md (measured tier sizes)
//
// Agent ownership:
//   Agent A — backend (GET /graph/decomposition, ?layout=, jobs)
//   Agent B — Atlas icicle, subjects-pane.tsx
//   Agent C — Constellation, drag-heal, saved-view v3 read/write

// ---------------------------------------------------------------------------
// 1. GET /graph/decomposition — full payload
// ---------------------------------------------------------------------------

/** Top-degree entity within a community, ordered by degree descending. */
export interface CommunityHub {
  id: string
  name: string
  degree: number
}

/** A single L0 (finest) community node in the tree. */
export interface L0Community {
  id: string
  /** Mode-domain color key. Matches a domainColor() registry key. */
  mode_domain: string
  /**
   * Domain purity: fraction of entities whose primary_domain equals
   * mode_domain. 1.0 = single-domain pure. <0.70 = mixed (show split-tint).
   */
  purity: number
  /** Number of entities in this community. */
  size: number
  /**
   * Human-readable label. May be absent when Community.summary is not yet
   * generated (label debt). Clients MUST apply the fallback-label strategy:
   * 1. If absent or matches NUMERIC_GARBAGE_RE → build deterministic fallback
   *    "Community of N — top entities: A, B, C" (members ordered by degree).
   * 2. A6: same regex demotion applies to served labels.
   */
  label?: string
  /** Top-degree hub entities. Always present; used for fallback labels. */
  top_hubs: CommunityHub[]
}

/** Rollup bucket for all L0 communities of size < 4 under one L1 parent. */
export interface L0RollupBucket {
  /** Discriminant — always "rollup". */
  kind: "rollup"
  /** Number of small communities collapsed into this bucket. */
  community_count: number
  /** Total entities across collapsed communities. */
  entity_count: number
  /**
   * The rolled-up member communities (UX-13). Render as drillable rows —
   * the bucket must never be an inert count.
   */
  communities: L0Community[]
}

/** A single L1 (coarser) community node, parent of a set of L0 communities. */
export interface L1Community {
  id: string
  mode_domain: string
  purity: number
  size: number
  label?: string
  top_hubs: CommunityHub[]
  /** L0 children. May include a trailing RollupBucket (size < 4 groups). */
  children: (L0Community | L0RollupBucket)[]
}

/**
 * SubCategory tier node. Rendered only when a domain has ≥2 live
 * subcategories with entities (currently finance + marginally coding).
 * Absent on most domains — clients must treat the tier as optional.
 */
export interface SubCategoryNode {
  id: string
  label: string
  /** Number of entities with this subcategory. */
  entity_count: number
  children: L1Community[]
}

/** Per-domain "Unclustered (N)" bucket for entities not in any community. */
export interface UnclusteredBucket {
  /** Number of entities in this domain with no community membership. */
  count: number
}

/** A single T0 domain node — root of the icicle tree. */
export interface DomainNode {
  /** Primary domain key, e.g. "research", "coding". */
  id: string
  label: string
  /** Total entities in this domain (clustered + unclustered). */
  entity_count: number
  unclustered: UnclusteredBucket
  /**
   * SubCategory children. Present only when the domain has ≥2 live
   * subcategories. When absent, L1 communities are direct children.
   */
  subcategories?: SubCategoryNode[]
  /** Direct L1 children. Present when subcategories is absent. */
  communities?: L1Community[]
}

/**
 * Full GET /graph/decomposition payload.
 *
 * A3: no_communities_computed — true when Leiden has never run (zero
 * Community nodes in the graph). Clients MUST render the honest
 * two-tier degradation: Domain → Entity list with inline notice
 * "Clusters appear after the nightly analysis runs".
 * Never invent a fake tree. Distinct from empty_kb (no entities at all).
 */
export interface DecompositionPayload {
  /**
   * T0 domain rows. Always present (even pre-Leiden). Includes all
   * entity-bearing domains (11 on live data) plus a synthetic
   * "uncategorized" strip for the 32 orphan entities.
   */
  domains: DomainNode[]
  /**
   * Flat map of L0 community id → L1 community id. Derived from
   * co-membership (no PARENT_OF edge). Used for path walks.
   */
  parent_map: Record<string, string>
  /** Number of entities with no primary_domain (orphans). */
  uncategorized_count: number
  /**
   * A3: true when Leiden has never run. Icicle degrades to
   * Domain → Entity two-tier. Community tiers must not be rendered.
   */
  no_communities_computed: boolean
  /** ISO timestamp of the last DecompositionJob run. */
  computed_at: string | null
  cached: boolean
}

/**
 * Per-entity leaf shape returned by GET /graph/decomposition?community=<id>.
 * Used for virtualized T4 entity lists.
 */
export interface EntityLeaf {
  id: string
  name: string
  type: string
  trust_state: string
  /**
   * Full tier path for this entity — used by search-palette path-walk.
   * Format: [domain, sub?, l1, l0] (sub is optional per SubCategory rule).
   * Exactly matches the tiers rendered in the icicle.
   */
  path: string[]
}

export interface DecompositionCommunityPayload {
  community_id: string
  entities: EntityLeaf[]
}

/**
 * GET /graph/decomposition?bucket=<kind>[&domain=<id>] payload (UX-13).
 * Drill path for the non-community buckets: "unclustered" (per domain)
 * and "uncategorized" (global).
 */
export interface DecompositionBucketPayload {
  bucket: "unclustered" | "uncategorized"
  domain: string | null
  entities: EntityLeaf[]
}

// ---------------------------------------------------------------------------
// 2. GET /graph/map — layout extensions
// ---------------------------------------------------------------------------

/**
 * Layout values accepted by GET /graph/map?layout=.
 * Omitting the param is byte-identical to "force".
 * Unknown values → 422.
 */
export type MapLayout = "force" | "wells" | "domain"

// added: Living-Map A7 (2026-07-07) — the server gained the "semantic"
// (PaCMAP embedding-space) layout preset. MapLayout above stays frozen;
// consumers migrate to MapLayoutV2. The layout_fallback machinery in
// GraphMapLayoutExtensions covers a not-yet-computed semantic artifact.
export type MapLayoutV2 = MapLayout | "semantic"

/**
 * GET /graph/map response shape additions for Cycle 4.
 * Extends the existing GraphMapResponse from lib/api/graph-map.ts.
 * Agent C merges these into the response type they own.
 */
export interface GraphMapLayoutExtensions {
  /**
   * The layout that was actually served. May differ from the requested
   * layout when the artifact is not yet computed — in that case this is
   * "force" and layout_fallback is true.
   */
  layout: MapLayout
  /**
   * True when the requested non-default layout artifact was missing and
   * the server fell back to "force". Client should toast:
   * "Layout still computing — showing default".
   */
  layout_fallback: boolean
}

// ---------------------------------------------------------------------------
// 3. Saved-view v3 additions
// ---------------------------------------------------------------------------

export const ATLAS_VIEW_VERSION_V3 = 3 as const

/**
 * A3 amendment: decomposition ladder position for saved Atlas views.
 * Optional — absent means no ladder state was saved (e.g. view was saved
 * from Neighborhood mode or pre-v3). On restore, client walks the served
 * path exactly like a search-palette pick.
 */
export interface AtlasTierPosition {
  /**
   * Tier path from root to the deepest expanded tier, e.g.:
   *   ["research"]                  — domain expanded
   *   ["research", "0:1234"]        — domain + L1
   *   ["research", "sub/beir", "0:1234"] — domain + subcategory + L1
   */
  path: string[]
  /**
   * Depth index (0 = T0, 1 = SubCategory/L1, 2 = L0, 3 = entity list).
   * Mirrors the T0–T4 tier numbering in the icicle spec.
   */
  depth: number
}

/**
 * Saved-view v3 additions (all fields optional — v0–v2 views unaffected).
 * Agent C merges these into AtlasViewInput alongside layout/viewDim/camera3d.
 */
export interface AtlasViewV3Extensions {
  /** Cycle 4 layout base — see MapLayout. */
  layout?: MapLayout
  /** View dimension: "map" = 2D sigma, "3d" = R3F scene. */
  viewDim?: "map" | "3d"
  /** 3D camera state (R3F OrbitControls). */
  camera3d?: {
    position: [number, number, number]
    target: [number, number, number]
  }
  /** Pinned node overrides: entity id → { x, y } in map coordinates. */
  pinnedNodes?: Record<string, { x: number; y: number }>
  /**
   * A2: decomposition ladder position. Agent B writes this on save;
   * restore walks the path exactly like a search-palette pick.
   */
  atlasTier?: AtlasTierPosition
}

// ---------------------------------------------------------------------------
// 4. Unified click contract — onInspect / onFocusEntity
// ---------------------------------------------------------------------------

/**
 * Pin an entity card without changing surface, mode, or focal context.
 * Consumed by Atlas icicle entity rows, Atlas Neighborhood node clicks,
 * and Cartographer 2D node clicks.
 *
 * Graph surfaces receive this prop in place of the old onNodeClick that
 * conflated inspection with mode-switching.
 *
 * Caller (subjects-pane.tsx): sets pinned entity state only.
 * Agent B implements the split; Agent C consumes the props.
 */
export type OnInspect = (entityId: string) => void

/**
 * Explicit refocus request — re-centers the graph on this entity and
 * triggers a neighborhood fetch, but does NOT switch the active mode or
 * surface. Invoked by: entity card "Make focal" button, hull-card hub
 * buttons, search-palette picks.
 *
 * Caller (subjects-pane.tsx): sets focalEntity + refetches neighborhood,
 * preserving current mode.
 */
export type OnFocusEntity = (entityId: string) => void

/**
 * Prop bundle for graph surfaces that implement the unified click contract.
 * Agent B provides these from subjects-pane.tsx; Agent C consumes them in
 * CartographerMap.tsx. The Atlas icicle and Neighborhood component use the
 * same props.
 */
export interface ClickContractProps {
  onInspect: OnInspect
  onFocusEntity: OnFocusEntity
}

// ---------------------------------------------------------------------------
// 5. createHealController — type contract (A1)
//    Agent C implements in lib/graph/interactions/drag-heal.ts.
//    Agent B types only — no implementation here.
// ---------------------------------------------------------------------------

/**
 * Options for the renderer-agnostic drag-heal controller (Amendment A1).
 * Agent C implements this in lib/graph/interactions/drag-heal.ts.
 * The controller is mounted once (Cartographer 2D in v1); Atlas Neighborhood
 * and 3D are v1.1 consumers.
 */
export interface HealControllerOptions<TPos> {
  /** Return the authoritative server position for a node by id. */
  getHome: (nodeId: string) => TPos
  /** Return the current rendered position for a node by id. */
  getPos: (nodeId: string) => TPos
  /** Write a new rendered position for a node by id. */
  setPos: (nodeId: string, pos: TPos) => void
  /** Return 1-hop neighbor ids for a node. */
  neighbors: (nodeId: string) => string[]
  /**
   * Called once when all nodes have settled back to their home positions
   * (or snapped, under reduced-motion). Trigger a full graph refresh here.
   */
  onSettle: () => void
  /**
   * When true: no settle animation — nodes snap home instantly on release
   * (single-frame). Drag-follow itself is unaffected (direct manipulation).
   * Driven by window.matchMedia("(prefers-reduced-motion: reduce)").
   */
  reducedMotion: boolean
}

/**
 * Handle returned by createHealController (Agent C).
 * Consumers call these in their pointer-event handlers.
 */
export interface HealController {
  /** Call on pointer-down over a node. Begins a drag session. */
  startDrag: (nodeId: string) => void
  /**
   * Call on pointer-move during drag. dx/dy are displacement from the
   * drag-start position in graph coordinates.
   */
  moveDrag: (nodeId: string, pos: { x: number; y: number }) => void
  /**
   * Call on pointer-up / pointer-cancel.
   * Without Shift: begins critically-damped lerp-home for node + neighbors.
   * With Shift: pins node at current position (no spring-back).
   * Interruptible: a new startDrag mid-heal cancels the in-flight animation.
   */
  endDrag: (nodeId: string, opts?: { pin?: boolean }) => void
  /** Cancel any in-flight heal animation without settling. */
  cancel: () => void
}

/**
 * Factory signature for the drag-heal controller.
 * Agent C implements; Agents A and B reference this type only.
 *
 * The 2D sigma adapter wraps this with sigma coordinate transforms.
 * The 3D adapter wraps this with R3F/Three.js pointer-ray intersection.
 */
export type CreateHealController = (
  options: HealControllerOptions<{ x: number; y: number }>,
) => HealController

// ---------------------------------------------------------------------------
// 6. Client-side constants
// ---------------------------------------------------------------------------

/**
 * A6: regex for numeric/garbage community labels that must be demoted to
 * the deterministic fallback. Matches labels that are purely numeric,
 * fractional, or empty after trim.
 *
 * Examples that match (demote): "0.7143", "42", "  ", ""
 * Examples that do not match (keep): "Quantum Computing", "Community of 3"
 */
export const NUMERIC_GARBAGE_RE = /^\s*[\d.]+\s*$|^\s*$/

/**
 * Build a deterministic fallback label for a community.
 * Used when Community.summary is absent or the served label matches
 * NUMERIC_GARBAGE_RE (A6). Member hubs must be pre-sorted by degree
 * descending so the label is stable across sessions.
 */
export function buildFallbackLabel(size: number, topHubs: CommunityHub[]): string {
  const names = topHubs
    .slice(0, 3)
    .map((h) => h.name)
    .join(", ")
  return names ? `Community of ${size} — top entities: ${names}` : `Community of ${size}`
}

/**
 * A4: maximum hops value promoted in the UI.
 * hops=3 remains URL-reachable but must not appear in the stepper.
 */
export const NEIGHBORHOOD_HOPS_MAX_PROMOTED = 2 as const
