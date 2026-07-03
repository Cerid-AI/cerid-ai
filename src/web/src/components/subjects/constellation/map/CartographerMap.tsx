// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Cartographer — flat 2D knowledge-map scene built on sigma.js v3.
//
// Design rules (non-negotiable):
//   - FLAT. No glow, bloom, lighting, starfield, gradients.
//   - Background = theme --background token.
//   - Teal ONLY for interaction state (hover ring, selection, search hit).
//   - Node fill = active lens via sigma nodeReducer (no raw hex in JSX).
//   - Node radius = clamp(2 + 1.2 * sqrt(degree), 2, 16)
//   - Trust = border ring via neutral foreground ramp, NOT fill.
//   - Edges under nodes, ~12% alpha, background-mixed color, weight → width 1–2.5px.
//   - Labels: zoom-gated LOD only.
//   - Static positions from API — no client physics.
//   - hideEdgesOnMove: true.

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Sigma from "sigma"
import Graph from "graphology"
import EdgeCurveProgram from "@sigma/edge-curve"
import { createNodeBorderProgram } from "@sigma/node-border"
import { NodeCircleProgram, createNodeCompoundProgram } from "sigma/rendering"
import type { NodeProgramType } from "sigma/rendering"
import { Loader2, X, Link2, Users, ShieldCheck, Maximize2 } from "lucide-react"
import type { GraphMapResponse, CommunityHull, MapLayout } from "@/lib/api/graph-map"
import type { MapConfig } from "./map-config"
import { LABEL_DENSITY_VALUES } from "./map-config"
import { useCommunityLayer, resolveMapTokens, type MapTokens } from "./community-layer"
import { useSuperNodeLayer } from "./community-supernodes"
import { buildLevelCommunities, buildLevelSuperEdges } from "./community-hierarchy-levels"
import { useCommunityHierarchy } from "./use-community-hierarchy"
import { useHighlightEdges } from "./highlight-edges"
import { makeDrawNodeHover } from "@/lib/graph/draw-node-hover"
import { HOVER_INTENT_DELAY_MS } from "@/lib/graph/hover-intent"
import { useNavigation } from "@/contexts/navigation-context"
import { domainColor } from "@/lib/graph/identity"
import { createHealController } from "@/lib/graph/interactions/drag-heal"
import { nodeBaseAlpha, ISOLATED_COMMUNITY_ID } from "../palette"
import type { OnInspect, OnFocusEntity } from "@/lib/graph/cycle4-contracts"
import { useForceLayout } from "./use-force-layout"
import { matchesSearch } from "./filter-predicates"
import { cameraTargetForPoints, lodTier, lodEdgeMinSize } from "./semantic-zoom"
// ---------------------------------------------------------------------------
// 2D cathedral programs — mirroring atlas-programs.ts (3D uses same libs).
// Subtle 1px border (trust ring); curved edges at 0.25 curvature.
// ---------------------------------------------------------------------------

const CartoBorderProgram = createNodeBorderProgram({
  borders: [
    {
      size: { value: 1, mode: "pixels" },
      color: { attribute: "borderColor", defaultValue: "#888888" }, // drift-allowed: neutral fallback
    },
    {
      size: { fill: true },
      color: { attribute: "color", defaultValue: "#5C6680" }, // drift-allowed: graphite fallback
    },
  ],
})

const CARTO_NODE_PROGRAM: NodeProgramType = createNodeCompoundProgram([
  CartoBorderProgram as unknown as NodeProgramType,
  NodeCircleProgram as unknown as NodeProgramType,
])

const CARTO_NODE_PROGRAM_CLASSES: Record<string, NodeProgramType> = {
  bordered: CARTO_NODE_PROGRAM,
}

const CARTO_EDGE_PROGRAM_CLASSES = {
  curve: EdgeCurveProgram,
} as const

// sigma's MouseCoords — local interface matching the vendored type
interface SigmaMouseCoords {
  x: number
  y: number
  original: MouseEvent | TouchEvent
  preventSigmaDefault: () => void
  sigmaDefaultPrevented: boolean
}

// ---------------------------------------------------------------------------
// Sizing
// ---------------------------------------------------------------------------

/** Node radius formula from spec: clamp(2 + 1.2 * sqrt(degree), 2, 16) */
function nodeRadius(degree: number): number {
  return Math.min(16, Math.max(2, 2 + 1.2 * Math.sqrt(Math.max(0, degree))))
}

/** Edge width formula: weight → 1–2.5px clamped */
function edgeWidth(weight: number): number {
  return Math.min(2.5, Math.max(1, 1 + (weight / 10) * 1.5))
}

/** Ray-casting point-in-polygon over a hull (graph coordinates). */
function pointInHull(x: number, y: number, poly: [number, number][]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]
    const [xj, yj] = poly[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside
    }
  }
  return inside
}

/** Andrew's monotone-chain convex hull. Returns the hull polygon (CCW). */
function convexHull(pts: [number, number][]): [number, number][] {
  if (pts.length < 3) return pts
  const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const cross = (o: [number, number], a: [number, number], b: [number, number]) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
  const lower: [number, number][] = []
  for (const pt of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], pt) <= 0) lower.pop()
    lower.push(pt)
  }
  const upper: [number, number][] = []
  for (let i = p.length - 1; i >= 0; i--) {
    const pt = p[i]
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], pt) <= 0) upper.pop()
    upper.push(pt)
  }
  lower.pop()
  upper.pop()
  return lower.concat(upper)
}

/** Build a per-domain region hull from entity positions, trimming outliers so
 *  a single stray node doesn't balloon the territory. Shaped as a CommunityHull
 *  so it flows through the same nebula overlay. */
function buildDomainHulls(
  entities: { x: number; y: number; primary_domain?: string | null }[],
): CommunityHull[] {
  const byDomain = new Map<string, [number, number][]>()
  for (const e of entities) {
    const dom = e.primary_domain
    if (!dom || e.x == null || e.y == null) continue
    const arr = byDomain.get(dom) ?? []
    arr.push([e.x, e.y])
    byDomain.set(dom, arr)
  }
  const hulls: CommunityHull[] = []
  for (const [dom, pts] of byDomain) {
    if (pts.length < 3) continue
    const cx = pts.reduce((s, q) => s + q[0], 0) / pts.length
    const cy = pts.reduce((s, q) => s + q[1], 0) / pts.length
    const kept = pts
      .map((q) => ({ q, d: Math.hypot(q[0] - cx, q[1] - cy) }))
      .sort((a, b) => a.d - b.d)
      .slice(0, Math.max(3, Math.floor(pts.length * 0.85)))
      .map((x) => x.q)
    const hull = convexHull(kept)
    if (hull.length < 3) continue
    hulls.push({ id: dom, count: pts.length, hull, anchor: [cx, cy], label: dom, top_hubs: [], trust_mix: {} })
  }
  return hulls
}

// Minimum interactive hit size in pixels — nodes that shrink past this floor
// become unclickable; enforce for any visible node.
const HIT_SIZE_MIN = 4

// ---------------------------------------------------------------------------
// Community color helpers
// ---------------------------------------------------------------------------

const CLUSTER_SLOTS = 8

function hashToSlot(id: string): number {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash) % CLUSTER_SLOTS
}

function clusterColor(communityId: string | null, tokens: MapTokens): string {
  if (!communityId || communityId === ISOLATED_COMMUNITY_ID) return tokens.graphite
  return tokens.clusters[hashToSlot(communityId)] ?? tokens.clusterOther
}

// ---------------------------------------------------------------------------
// Lens coloring
// ---------------------------------------------------------------------------

export type ColorLens = "cluster" | "trust" | "type" | "domain"

const TYPE_SLOT: Record<string, number> = {
  PERSON: 0, Person: 0,
  ORG: 1, Organization: 1,
  LOC: 2, Location: 2,
  EVENT: 3, Event: 3,
  ASSET: 4, Asset: 4,
}

function lensColor(
  entity: { type: string; community: string | null; trust_state: string; primary_domain?: string | null },
  lens: ColorLens,
  tokens: MapTokens,
): string {
  if (lens === "type") {
    const slot = TYPE_SLOT[entity.type]
    return slot !== undefined ? (tokens.clusters[slot] ?? tokens.clusterOther) : tokens.clusterOther
  }
  if (lens === "trust") {
    // Trust lens: use dedicated trust tokens; fall back to neutral dim when
    // no trust data is present (trust_state === "unknown" means no writer has
    // run yet — show an honest muted neutral rather than fake green).
    switch (entity.trust_state) {
      case "verified":     return tokens.trustVerified
      case "partial":      return tokens.trustPartial
      case "unverified":   return tokens.trustUnverified
      case "contradicted": return tokens.trustUnverified
      default:             return tokens.dim
    }
  }
  if (lens === "domain") {
    return domainColor(tokens, entity.primary_domain ?? null)
  }
  // cluster (default)
  return clusterColor(entity.community, tokens)
}

// ---------------------------------------------------------------------------
// Token equality check — avoid refreshing sigma when tokens are the same value
// ---------------------------------------------------------------------------

function tokensEqual(a: MapTokens, b: MapTokens): boolean {
  if (a === b) return true
  return (
    a.foreground === b.foreground &&
    a.background === b.background &&
    a.edge === b.edge &&
    a.dim === b.dim &&
    a.interaction === b.interaction &&
    a.clusterOther === b.clusterOther &&
    a.domainOther === b.domainOther &&
    a.trustVerified === b.trustVerified &&
    a.trustPartial === b.trustPartial &&
    a.trustUnverified === b.trustUnverified &&
    a.graphite === b.graphite &&
    a.fontSans === b.fontSans &&
    a.clusters.length === b.clusters.length &&
    a.clusters.every((c, i) => c === b.clusters[i]) &&
    a.domains.length === b.domains.length &&
    a.domains.every((c, i) => c === b.domains[i])
  )
}

// ---------------------------------------------------------------------------
// Pulse ring bookkeeping
// ---------------------------------------------------------------------------

/** A teal pulse ring that fades out over 600ms */
interface PulseEntry {
  nodeId: string
  startMs: number
}

const PULSE_DURATION_MS = 600

// Drag state refs — managed separately from React state to avoid reducer
// reinstalls on every pointer event.
interface DragRef {
  nodeId: string | null
  startX: number
  startY: number
  didDrag: boolean
}

// Click-vs-drag threshold: 4px displacement classifies as drag.
const DRAG_THRESHOLD_PX = 4

// ---------------------------------------------------------------------------
// Component props
// ---------------------------------------------------------------------------

export interface CartographerMapProps {
  focalEntity?: string
  filter?: string | null
  lens: ColorLens
  typeFilter: Set<string>
  config: MapConfig
  data: GraphMapResponse | undefined
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  newEntityIds?: Set<string>
  /**
   * Unified click contract (Cycle 4): pin/inspect only — never mode-switch.
   * Replaces the old onNodeClick that conflated inspection with navigation.
   */
  onInspect?: OnInspect
  /**
   * Explicit refocus: re-centers the graph on this entity.
   * Called by hull-card hub buttons only; node clicks use onInspect.
   */
  onFocusEntity?: OnFocusEntity
  onCommunityClick?: (community: CommunityHull) => void
  /**
   * The requested server layout ("force" | "wells" | "domain"). Drives the
   * position re-seed guard so a preset switch actually applies new coordinates.
   * (The server response does not echo the layout, so we thread the request.)
   */
  layout?: MapLayout
  /** layout_fallback: true → show inline "wells layout not computed yet" notice */
  layoutFallback?: boolean
  /**
   * Called when the user Shift-drops a node to permanently pin it.
   * Parent can persist this into the saved view's pinnedNodes field.
   */
  onPinnedNodesChange?: (pinnedNodes: Record<string, { x: number; y: number }>) => void
  /** Live search query — non-matching nodes dim to the search-miss state. */
  search?: string
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CartographerMap({
  filter,
  lens,
  typeFilter,
  config,
  data,
  isLoading,
  isError,
  errorMessage,
  newEntityIds,
  onInspect,
  onFocusEntity: _onFocusEntity, // eslint-disable-line @typescript-eslint/no-unused-vars
  onCommunityClick,
  layout,
  layoutFallback,
  onPinnedNodesChange,
  search,
}: CartographerMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const onInspectRef = useRef(onInspect)
  onInspectRef.current = onInspect

  const { goTo } = useNavigation()

  const [sigmaInstance, setSigmaInstance] = useState<Sigma | null>(null)
  const [tokens, setTokens] = useState<MapTokens>(() => {
    if (typeof document !== "undefined") {
      return resolveMapTokens(document.documentElement)
    }
    // SSR/test fallback — document not available; real tokens read via getComputedStyle
    return {
      clusters: Array(8).fill("#999"), // drift-allowed: SSR fallback only, never reaches browser
      clusterOther: "#999", // drift-allowed: SSR fallback only, never reaches browser
      domains: Array(12).fill("#999"), // drift-allowed: SSR fallback only, never reaches browser
      domainOther: "#666", // drift-allowed: SSR fallback only, never reaches browser
      edge: "#ccc", // drift-allowed: SSR fallback only, never reaches browser
      dim: "#eee", // drift-allowed: SSR fallback only, never reaches browser
      interaction: "#00c8b4", // drift-allowed: SSR fallback only, never reaches browser
      foreground: "#111", // drift-allowed: SSR fallback only, never reaches browser
      background: "#f5f5f5", // drift-allowed: SSR fallback only, never reaches browser
      trustVerified: "#333", // drift-allowed: SSR fallback only, never reaches browser
      trustPartial: "#555", // drift-allowed: SSR fallback only, never reaches browser
      trustUnverified: "#888", // drift-allowed: SSR fallback only, never reaches browser
      graphite: "#6b7080", // drift-allowed: SSR fallback only, never reaches browser
      grid: "#eee", // drift-allowed: SSR fallback only, never reaches browser
      fontSans: "system-ui, sans-serif", // drift-allowed: SSR fallback only, never reaches browser
    }
  })

  // Pinned entity card state
  const [pinnedId, setPinnedId] = useState<string | null>(null)
  // Drill-down (Model B): double-click a community region to isolate its
  // members (the rest of the map recedes); Esc / empty double-click exits.
  const [drilledCommunityId, setDrilledCommunityId] = useState<string | null>(null)
  const drilledCommunityIdRef = useRef<string | null>(null)
  drilledCommunityIdRef.current = drilledCommunityId
  // Fresh region hulls (community OR domain, per active layout) for the
  // double-click-region drill hit-test. Assigned below once activeHulls exists.
  const communitiesRef = useRef<CommunityHull[]>([])
  // Pinned node position overrides (Shift-drop drag-pins)
  const [pinnedNodes, setPinnedNodes] = useState<Record<string, { x: number; y: number }>>({})
  const pinnedNodesRef = useRef(pinnedNodes)
  pinnedNodesRef.current = pinnedNodes
  const onPinnedNodesChangeRef = useRef(onPinnedNodesChange)
  onPinnedNodesChangeRef.current = onPinnedNodesChange

  // Hover state lives in a ref — sigma events update it without triggering
  // React re-renders that would reinstall reducers on every mousemove.
  const hoverIdRef = useRef<string | null>(null)
  // DOM tooltip state for the HTML overlay (separate from sigma hover plate)
  const [tooltipState, setTooltipState] = useState<{ id: string; x: number; y: number } | null>(null)
  // Intent-delay timer — cleared on leaveNode to suppress accidental hover-throughs.
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Drag state ref — no React state; updates don't trigger re-renders.
  const dragRef = useRef<DragRef>({ nodeId: null, startX: 0, startY: 0, didDrag: false })
  // Grab cursor state for the container
  const [isDragging, setIsDragging] = useState(false)
  // Reduced motion detection — optional chaining guards jsdom test env
  const reducedMotion =
    typeof window !== "undefined"
      ? (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false)
      : false

  // Tracks whether communities are collapsed — read by nodeReducer/edgeReducer
  // AND by the FA2 controller (keeps the sim paused while members are hidden).
  // Updated by useSuperNodeLayer's onCollapsedChange, never by React state.
  const collapsedRef = useRef(false)

  const forceCtrl = useForceLayout({
    sigma: sigmaInstance,
    enabled: config.liveLayout,
    reducedMotion,
    shouldStayPaused: () => collapsedRef.current,
  })
  const forceCtrlRef = useRef(forceCtrl)
  forceCtrlRef.current = forceCtrl

  // Pulse ring state: map from nodeId → start timestamp
  const [pulseMap, setPulseMap] = useState<Map<string, number>>(new Map())
  const pulseRafRef = useRef<number | null>(null)
  const pulseEntriesRef = useRef<PulseEntry[]>([])

  // Stable identity of the node SET (ids only, order-independent). The sigma
  // rebuild keys on this instead of `data` so a layout-preset switch — same
  // nodes, new x/y — does NOT reconstruct Sigma. Reconstructing on every layout
  // switch hits a sigma v3 bug: the second `new Sigma` in the same container
  // drops its node-program registration ("could not find a suitable program for
  // node type circle"). Positions are applied in place by a separate effect.
  const dataNodeKey = useMemo(
    () => (data ? data.entities.map((e) => e.id).slice().sort().join(",") : ""),
    [data],
  )

  // Re-read tokens when theme changes (watch for .dark class toggle)
  // Value-compare before setState to avoid unnecessary sigma rebuilds.
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const next = resolveMapTokens(document.documentElement)
      setTokens((prev) => tokensEqual(prev, next) ? prev : next)
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])

  // ---------------------------------------------------------------------------
  // Build / rebuild sigma graph when data or tokens change
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current
    if (!container || !data) return

    // Compute degree map from links
    const degreeMap = new Map<number, number>()
    for (const [s, t] of data.links) {
      degreeMap.set(s, (degreeMap.get(s) ?? 0) + 1)
      degreeMap.set(t, (degreeMap.get(t) ?? 0) + 1)
    }

    // Build graphology graph
    const graph = new Graph({ type: "undirected", multi: false, allowSelfLoops: false })

    for (let i = 0; i < data.entities.length; i++) {
      const e = data.entities[i]
      const deg = degreeMap.get(i) ?? 0
      // Border color: neutral foreground ramp keyed on trust state (subtle ≤1px).
      const borderColor =
        e.trust_state === "verified"     ? tokens.foreground :
        e.trust_state === "partial"      ? tokens.graphite :
        e.trust_state === "contradicted" ? tokens.graphite :
        tokens.dim  // unverified / unknown → quietest ring
      graph.addNode(e.id, {
        x: e.x,
        y: e.y,
        label: e.name,
        size: nodeRadius(deg),
        color: lensColor(e, lens, tokens),
        borderColor,
        // Store original data for reducer access
        _degree: deg,
        _trust: e.trust_state,
        _community: e.community ?? "",
        _type: e.type,
        _mention_count: e.mention_count,
      })
    }

    // Edge budget: sort by weight descending, dedupe, then apply cap.
    const allLinks = [...data.links]
    allLinks.sort((a, b) => b[2] - a[2])

    // Dedupe reciprocal pairs BEFORE the budget slice — A→B and B→A are the
    // same edge in this undirected graph, so keeping both wastes budget and
    // makes graphology throw "edge already exists" mid-build.
    const seenPairs = new Set<string>()
    const dedupedLinks = allLinks.filter(([si, ti]) => {
      const canonical = si < ti ? `${si}--${ti}` : `${ti}--${si}`
      if (seenPairs.has(canonical)) return false
      seenPairs.add(canonical)
      return true
    })

    let budget: number
    if (config.edgeBudget === "off") budget = 0
    else if (config.edgeBudget === "2k") budget = 2000
    else if (config.edgeBudget === "all") budget = dedupedLinks.length
    else budget = 8000  // "8k" default

    const links = dedupedLinks.slice(0, budget)

    // Alpha suffix: 0x38 / 255 ≈ 0.22 — edges are the primary relational
    // signal; raising alpha from the former near-invisible value makes
    // them readable at a glance. similar edges get a dimmer suffix (0x28
    // ≈ 0.157) as a calm secondary tone. Both suffixes appended to the
    // token hex from resolveMapTokens, which always returns #RRGGBB.
    // Softer than before (was 0x38/0x28) so the dense edge web reads as a calm
    // underlay when zoomed in rather than overwhelming the nodes; hover-focus +
    // the highlight-edge layer surface the relevant connections on demand.
    const CO_MENTION_ALPHA_SUFFIX = "26"  // ≈ 15% — primary signal
    const SIMILAR_ALPHA_SUFFIX = "1a"     // ≈ 10% — secondary/dimmer

    for (const [si, ti, weight, kind] of links) {
      const src = data.entities[si]
      const tgt = data.entities[ti]
      if (!src || !tgt) continue
      if (!graph.hasNode(src.id) || !graph.hasNode(tgt.id)) continue
      // Canonical (direction-independent) key so the undirected edge can't be
      // added twice under two different directional keys.
      const key = src.id < tgt.id ? `${src.id}::${tgt.id}` : `${tgt.id}::${src.id}`
      if (graph.hasEdge(key)) continue
      const alphaSuffix = kind === "similar" ? SIMILAR_ALPHA_SUFFIX : CO_MENTION_ALPHA_SUFFIX
      graph.addEdgeWithKey(key, src.id, tgt.id, {
        size: edgeWidth(weight),
        color: tokens.edge + alphaSuffix,
        curvature: 0.25,
        // Store kind for the edgeReducer to use in focus styling.
        _kind: kind ?? "co_mention",
      })
    }

    // Tear down any existing sigma instance
    if (sigmaRef.current) {
      sigmaRef.current.kill()
      sigmaRef.current = null
      setSigmaInstance(null)
    }

    const sigma = new Sigma(graph, container, {
      renderLabels: true,
      labelSize: 11,
      labelWeight: "400",
      labelFont: tokens.fontSans,
      // Default colors from tokens — no hex literals
      defaultNodeColor: tokens.clusterOther,
      defaultEdgeColor: tokens.edge,
      labelColor: { color: tokens.foreground },
      labelDensity: LABEL_DENSITY_VALUES[config.labelDensity],
      labelGridCellSize: 80,
      labelRenderedSizeThreshold: 6,
      hideEdgesOnMove: true,
      // Edges rendered under nodes
      zIndex: true,
      // Theme-aware hover plate (overrides sigma's hardcoded #FFF default)
      defaultDrawNodeHover: makeDrawNodeHover(tokens),
      // Cathedral aesthetics: curved edges + node halos (FLAT rule — no bloom/glow)
      defaultEdgeType: "curve",
      edgeProgramClasses: CARTO_EDGE_PROGRAM_CLASSES,
      defaultNodeType: "bordered",
      nodeProgramClasses: CARTO_NODE_PROGRAM_CLASSES,
      // Smaller per-wheel zoom factor → finer, smoother zoom steps (default 1.7
      // felt steppy). Bound the camera so the user can't zoom into the void with
      // no way back — combined with the Reset button this prevents "lost in space".
      zoomingRatio: 1.3,
      minCameraRatio: 0.08,
      // 5.0 stays above the A8 multi-level collapse bands (coarsest ~4.4) while
      // still bounding zoom-out; the spread transform keeps the collapsed
      // overview viewport-filled, so a high cap never strands the user.
      maxCameraRatio: 5.0,
    })

    sigmaRef.current = sigma
    setSigmaInstance(sigma)

    // Build entity lookup for home positions (used by heal controller)
    const entityById = new Map(data.entities.map((e) => [e.id, e]))

    // Heal controller — one per sigma instance, disposed on teardown.
    const healCtrl = createHealController({
      getHome: (id) => {
        const e = entityById.get(id)
        return e ? { x: e.x, y: e.y } : { x: 0, y: 0 }
      },
      getPos: (id) => {
        if (!sigma.getGraph().hasNode(id)) return { x: 0, y: 0 }
        const attrs = sigma.getGraph().getNodeAttributes(id)
        return { x: attrs.x as number, y: attrs.y as number }
      },
      setPos: (id, pos) => {
        if (!sigma.getGraph().hasNode(id)) return
        sigma.getGraph().setNodeAttribute(id, "x", pos.x)
        sigma.getGraph().setNodeAttribute(id, "y", pos.y)
      },
      neighbors: (id) => {
        const g = sigma.getGraph()
        if (!g.hasNode(id)) return []
        const nbIds: string[] = []
        g.forEachNeighbor(id, (n) => nbIds.push(n))
        return nbIds
      },
      onSettle: () => {
        // Full refresh on settle to rebuild quadtree for hit-testing.
        sigma.refresh()
      },
      reducedMotion,
    })

    // ---------------------------------------------------------------------------
    // downNode — begin drag (beside clickNode per sigma grounding doc)
    // ---------------------------------------------------------------------------
    sigma.on("downNode", ({ node, event, preventSigmaDefault }) => {
      const orig = event.original as MouseEvent | TouchEvent
      const clientX = "clientX" in orig ? (orig as MouseEvent).clientX : 0
      const clientY = "clientY" in orig ? (orig as MouseEvent).clientY : 0

      dragRef.current = { nodeId: node, startX: clientX, startY: clientY, didDrag: false }
      preventSigmaDefault()

      setIsDragging(true)
      healCtrl.startDrag(node)
      forceCtrlRef.current.pause()
    })

    // ---------------------------------------------------------------------------
    // mousemovebody — drag move (MouseCoords per sigma captor contract)
    // ---------------------------------------------------------------------------
    const mouseCaptor = sigma.getMouseCaptor()
    const handleMouseMove = (coords: SigmaMouseCoords) => {
      const drag = dragRef.current
      if (!drag.nodeId) return

      const orig = coords.original as MouseEvent
      const clientX = orig.clientX ?? 0
      const clientY = orig.clientY ?? 0

      const dx = clientX - drag.startX
      const dy = clientY - drag.startY
      if (!drag.didDrag && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return

      dragRef.current.didDrag = true

      const graphPos = sigma.viewportToGraph({ x: coords.x, y: coords.y })
      healCtrl.moveDrag(drag.nodeId, graphPos)
      sigma.refresh({ skipIndexation: true })
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mouseCaptor.on("mousemovebody", handleMouseMove as any)

    // ---------------------------------------------------------------------------
    // mouseup — end drag or fire click (MouseCoords)
    // ---------------------------------------------------------------------------
    const handleMouseUp = (coords: SigmaMouseCoords) => {
      const drag = dragRef.current
      if (!drag.nodeId) return

      const node = drag.nodeId
      const didDrag = drag.didDrag
      dragRef.current = { nodeId: null, startX: 0, startY: 0, didDrag: false }
      setIsDragging(false)

      if (didDrag) {
        const orig = coords.original as MouseEvent
        const isShift = orig.shiftKey ?? false
        // Plain drop: endDrag runs a lerp-home tween back to the server-computed
        // position; Shift-drop pins in place. Do NOT reheat FA2 here — a global
        // warm would race the tween and can freeze the node off-target (and it
        // disturbs the whole graph). The heal owns the dropped node's return.
        healCtrl.endDrag(node, { pin: isShift })
        if (isShift) {
          const attrs = sigma.getGraph().getNodeAttributes(node)
          setPinnedNodes((prev) => {
            const next = { ...prev, [node]: { x: attrs.x as number, y: attrs.y as number } }
            onPinnedNodesChangeRef.current?.(next)
            return next
          })
        }
      } else {
        // Single click: pin + focus-zoom IN THE GRAPH only (explore-first).
        // The analysis drawer now opens on double-click, so a plain click keeps
        // exploration in the map (focus + connectivity highlight) instead of
        // surfacing an occluding wiki card that steals the whole view.
        const wasAlreadyPinned = pinnedIdRef.current === node
        setPinnedId((prev) => (prev === node ? null : node))
        if (!wasAlreadyPinned) focusCameraOnRef.current?.(node)
      }
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mouseCaptor.on("mouseup", handleMouseUp as any)

    // ---------------------------------------------------------------------------
    // clickNode — consumed by downNode path above; listen for non-drag clicks
    // that sigma still emits when preventSigmaDefault was NOT called.
    // (downNode always calls it, so this is a safety net for accessibility.)
    // ---------------------------------------------------------------------------
    sigma.on("clickNode", ({ node }) => {
      if (dragRef.current.nodeId !== null) return // already handled by mouseup
      const wasAlreadyPinned = pinnedIdRef.current === node
      setPinnedId((prev) => (prev === node ? null : node))
      if (!wasAlreadyPinned) focusCameraOnRef.current?.(node)
    })

    // doubleClickNode — deliberate deep-dive: pin the node and open the
    // analysis drawer. Kept off single-click so exploring the graph doesn't
    // immediately throw up an occluding card. preventSigmaDefault stops
    // sigma's built-in double-click zoom from fighting the focus camera.
    sigma.on("doubleClickNode", ({ node, preventSigmaDefault }) => {
      preventSigmaDefault()
      setPinnedId(node)
      onInspectRef.current?.(node)
    })

    // doubleClickStage — double-click empty map space inside a community region
    // DRILLS into that community: isolate its members + frame it. Double-click
    // the same region again, empty space, or Esc exits the drill. (Nodes keep
    // their own single=explore / double=wiki behaviour above; stage only fires
    // when the click misses every node.)
    sigma.on("doubleClickStage", ({ event, preventSigmaDefault }) => {
      preventSigmaDefault()
      const gp = sigma.viewportToGraph({ x: event.x, y: event.y })
      let hit: CommunityHull | null = null
      for (const c of communitiesRef.current) {
        if (c.hull.length >= 3 && pointInHull(gp.x, gp.y, c.hull)) {
          hit = c
          break
        }
      }
      if (hit) {
        const target = hit
        setDrilledCommunityId((prev) => (prev === target.id ? null : target.id))
        if (drilledCommunityIdRef.current !== target.id) {
          focusCameraOnPointsRef.current?.(target.hull)
        }
      } else {
        setDrilledCommunityId(null)
      }
    })

    sigma.on("enterNode", ({ node, event }) => {
      hoverIdRef.current = node
      recomputeFocusNeighbors()
      sigma.refresh({ skipIndexation: true })
      const orig = event.original
      const clientX = "clientX" in orig ? (orig as MouseEvent).clientX : 0
      const clientY = "clientY" in orig ? (orig as MouseEvent).clientY : 0
      // Intent delay — show card only after dwell (mirrors Atlas; avoids hover-through flicker)
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
      hoverTimerRef.current = setTimeout(() => {
        setTooltipState({ id: node, x: clientX, y: clientY })
      }, HOVER_INTENT_DELAY_MS)
    })
    sigma.on("leaveNode", () => {
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
      hoverIdRef.current = null
      recomputeFocusNeighbors()
      sigma.refresh({ skipIndexation: true })
      setTooltipState(null)
    })

    // ---------------------------------------------------------------------------
    // LOD camera listener — refresh ONLY on tier change (the tier-change guard
    // prevents a refresh storm on every camera move).
    // ---------------------------------------------------------------------------
    const camera = sigma.getCamera()
    const onCameraUpdate = () => {
      const tier = lodTier(camera.ratio)
      if (tier !== lodTierRef.current) {
        lodTierRef.current = tier
        sigma.refresh({ skipIndexation: true })
      }
    }
    camera.on("updated", onCameraUpdate)
    // Sync the initial tier so first paint uses the correct floor.
    onCameraUpdate()

    return () => {
      camera.off("updated", onCameraUpdate)
      healCtrl.dispose()
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      mouseCaptor.off("mousemovebody", handleMouseMove as any)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      mouseCaptor.off("mouseup", handleMouseUp as any)
      sigmaRef.current?.kill()
      sigmaRef.current = null
    }
    // Rebuild only when the node SET (dataNodeKey) or core styling changes —
    // NOT on a layout-preset switch (same nodes, new x/y), which is applied in
    // place by the position-sync effect below. This avoids the sigma v3
    // second-construction program-registration bug (see dataNodeKey above).
    // Lens/filter changes are handled by the reducer effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataNodeKey, config.edgeBudget, tokens])

  // ---------------------------------------------------------------------------
  // Layout-preset switch (force/wells/domain): same nodes, new coordinates.
  // Update x/y in place on the live graph and refresh — no Sigma reconstruction.
  // The seed guard keys on NODE SET *and* the served layout: a routine 75s
  // refetch (same nodes, same layout) is ignored so it can't yank nodes back
  // mid-breath, but a preset switch (same nodes, new layout) re-seeds. Keying on
  // node set alone made every preset a no-op — the coordinates were dropped.
  // ---------------------------------------------------------------------------
  const seededKeyRef = useRef("")
  useEffect(() => {
    const sigma = sigmaRef.current
    if (!sigma || !data) return
    const servedLayout = layout ?? "force"
    const seedKey = `${dataNodeKey}:${servedLayout}`
    if (seededKeyRef.current === seedKey) return
    const graph = sigma.getGraph()
    let moved = false
    for (const e of data.entities) {
      if (graph.hasNode(e.id)) {
        graph.setNodeAttribute(e.id, "x", e.x)
        graph.setNodeAttribute(e.id, "y", e.y)
        moved = true
      }
    }
    seededKeyRef.current = seedKey
    if (moved) sigma.refresh({ skipIndexation: false })
    // Only the default force layout gets the organic settle warm. The wells /
    // domain presets carry a deliberate arrangement (e.g. domains pulled apart);
    // re-warming FA2 would drag them back toward the community disc, so we apply
    // their coordinates and leave the sim frozen.
    if (servedLayout === "force") forceCtrlRef.current.reheat()
  }, [data, dataNodeKey, layout])

  // ---------------------------------------------------------------------------
  // Node/edge reducers for lens + filter + hover dimming (installed ONCE per
  // sigma instance). Reducers read hoverIdRef.current so they stay stable
  // without depending on hover state as a React value.
  // ---------------------------------------------------------------------------

  // Stable ref to lens/typeFilter/tokens/pinnedId so the reducers can read the
  // latest value without being re-installed on every change.
  const lensRef = useRef(lens)
  lensRef.current = lens
  const typeFilterRef = useRef(typeFilter)
  typeFilterRef.current = typeFilter
  const tokensRef = useRef(tokens)
  tokensRef.current = tokens
  const pinnedIdRef = useRef(pinnedId)
  pinnedIdRef.current = pinnedId
  const searchRef = useRef(search ?? "")
  searchRef.current = search ?? ""
  const configRef = useRef(config)
  configRef.current = config

  // (collapsedRef declared earlier, before useForceLayout, so the controller
  // can read it to stay paused while collapsed.)

  // LOD tier, updated by the camera "updated" listener. lodTierRef guards
  // sigma.refresh against a per-frame storm — only fires on tier change, not
  // every camera update (same pattern as A6 collapsed guard).
  const lodTierRef = useRef<"overview" | "mid" | "detail">("mid")

  // Stable ref to focusCameraOn so the sigma rebuild closure (which runs once
  // per dataNodeKey change) can call the latest version without being
  // re-installed on every reducedMotion change.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const focusCameraOnRef = useRef<((nodeId: string) => void) | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const focusCameraOnPointsRef = useRef<((pts: [number, number][]) => void) | null>(null)

  // Memoized focus neighbourhood: recomputed ONCE whenever the hover/pin
  // center changes (recomputeFocusNeighbors), then READ by the node/edge
  // reducers. Previously each reducer rebuilt this set per-element on every
  // refresh (O(degree×N) per hover frame) — the source of the hover jank.
  const focusNeighborsRef = useRef<Set<string> | null>(null)
  // Focus fade progress 0..1 — ramps up when a node is hovered/pinned and back
  // down on leave, so the neighbourhood emphasis eases in/out instead of
  // snapping (the "abrupt / jarring" hover the reducers read this to interpolate
  // the non-focus fade). reduced-motion jumps straight to the target.
  const focusProgressRef = useRef(0)
  const focusRafRef = useRef<number | null>(null)
  const FOCUS_FADE_MS = 180
  const rampFocusProgress = useCallback((target: number) => {
    if (focusRafRef.current !== null) {
      cancelAnimationFrame(focusRafRef.current)
      focusRafRef.current = null
    }
    if (reducedMotion) {
      focusProgressRef.current = target
      if (target === 0) focusNeighborsRef.current = null
      sigmaRef.current?.refresh({ skipIndexation: true })
      return
    }
    const start = performance.now()
    const from = focusProgressRef.current
    const step = () => {
      const t = Math.min(1, (performance.now() - start) / FOCUS_FADE_MS)
      // easeOutCubic for a soft settle
      const e = 1 - Math.pow(1 - t, 3)
      focusProgressRef.current = from + (target - from) * e
      if (t >= 1) {
        focusRafRef.current = null
        // Clear the held neighbourhood only AFTER the fade-out completes, so
        // non-focus nodes ease back to full rather than snapping.
        if (target === 0) focusNeighborsRef.current = null
      } else {
        focusRafRef.current = requestAnimationFrame(step)
      }
      sigmaRef.current?.refresh({ skipIndexation: true })
    }
    focusRafRef.current = requestAnimationFrame(step)
  }, [reducedMotion])
  const recomputeFocusNeighbors = useCallback(() => {
    const sigma = sigmaRef.current
    if (!sigma) {
      focusNeighborsRef.current = null
      return
    }
    const graph = sigma.getGraph()
    const focusCenter = pinnedIdRef.current ?? hoverIdRef.current ?? null
    if (focusCenter && graph.hasNode(focusCenter)) {
      const set = new Set<string>()
      graph.forEachNeighbor(focusCenter, (n) => set.add(n))
      set.add(focusCenter)
      focusNeighborsRef.current = set
      rampFocusProgress(1)
    } else {
      // Keep the prior neighbourhood set until the fade-out ramp finishes
      // (rampFocusProgress clears it at progress 0) so the un-dim is smooth.
      rampFocusProgress(0)
    }
  }, [rampFocusProgress])

  const pulseMapRef = useRef(pulseMap)
  pulseMapRef.current = pulseMap
  const dataRef = useRef(data)
  dataRef.current = data

  // Install reducers once per sigma instance; subsequent lens/filter/hover
  // changes are visible via refs — just call sigma.refresh().
  useEffect(() => {
    if (!sigmaInstance || !data) return

    const sigma = sigmaInstance

    // Build entity index once for this sigma instance
    const entityByIdMap = new Map(data.entities.map((e) => [e.id, e]))

    // Build degree map for edge hit-floor enforcement
    const degreeMap = new Map<number, number>()
    for (const [s, t] of data.links) {
      degreeMap.set(s, (degreeMap.get(s) ?? 0) + 1)
      degreeMap.set(t, (degreeMap.get(t) ?? 0) + 1)
    }

    sigma.setSetting("nodeReducer", (node, attrs) => {
      if (collapsedRef.current) return { ...attrs, hidden: true }
      const entity = entityByIdMap.get(node)
      if (!entity) return { ...attrs, hidden: true }

      // Drill-down (Model B): when a region is drilled, everything outside it is
      // hidden so only its members remain. A drilled community matches on
      // community id; a drilled domain region matches on primary_domain (the two
      // id spaces don't collide, so checking both is safe).
      if (
        drilledCommunityIdRef.current &&
        String(entity.community ?? "") !== drilledCommunityIdRef.current &&
        String(entity.primary_domain ?? "") !== drilledCommunityIdRef.current
      ) {
        return { ...attrs, hidden: true }
      }

      const currentTokens = tokensRef.current
      const currentLens = lensRef.current
      const currentTypeFilter = typeFilterRef.current
      const currentPinnedId = pinnedIdRef.current
      const hoverId = hoverIdRef.current
      const currentPulseMap = pulseMapRef.current
      const currentDragId = dragRef.current.nodeId
      const isPermanentPin = node in pinnedNodesRef.current

      // Focus neighbourhood is precomputed once per hover/pin change
      // (recomputeFocusNeighbors) — read it here instead of rebuilding the
      // set for every node on every refresh.
      const focusCenter = currentPinnedId ?? hoverId ?? null
      const focusNeighbors = focusNeighborsRef.current

      // Deliberate filters (instant, neutral-dim): type chip, search miss, and —
      // in the domain lens — entities with no domain (so the domained structure
      // reads instead of 66% neutral swamping it).
      const typeFiltered = currentTypeFilter.size > 0 && !currentTypeFilter.has(entity.type)
      const searchMiss = !matchesSearch(entity.name, searchRef.current)
      const domainMiss = currentLens === "domain" && !entity.primary_domain
      // Trust lens: recede the (large) "unknown" set so the actually-verified
      // nodes stand out — honest about where trust evidence exists.
      const trustMiss = currentLens === "trust" && (!entity.trust_state || entity.trust_state === "unknown")
      const orphanHidden = configRef.current.hideOrphans && ((attrs._degree as number) ?? 0) === 0

      const hasFocus = focusNeighbors !== null
      const inFocus = !hasFocus || focusNeighbors!.has(node)

      // Lens color + confidence alpha, computed up-front so the focus fade can
      // preserve hue (fade alpha) instead of graying out.
      const baseColor = lensColor(entity, currentLens, currentTokens)
      const baseAlpha = nodeBaseAlpha(entity.mention_count ?? 1)
      const withAlpha = (a: number) =>
        baseColor.startsWith("#") && baseColor.length === 7
          ? baseColor + Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, "0")
          : baseColor

      if (orphanHidden) {
        return { ...attrs, hidden: true }
      }
      // Explicit filters (type chips, search) HIDE non-matching entities so the
      // matching set reads on its own — a dim-only recede was too subtle to
      // register as a filter.
      if (typeFiltered || searchMiss) {
        return { ...attrs, hidden: true }
      }
      // Lens recede (no-domain in the domain lens, unknown in the trust lens) is
      // a softening of an irrelevant set, not a user filter — keep it a dim ghost
      // so the domained / trusted structure still reads against context.
      if (domainMiss || trustMiss) {
        return {
          ...attrs,
          color: currentTokens.dim,
          label: "",
          size: Math.max(HIT_SIZE_MIN, attrs.size * 0.6),
        }
      }
      // Hover/pin focus: non-neighbours EASE out — hue-preserving alpha fade +
      // gentle shrink, strength driven by focusProgressRef (0→1 over ~180ms) so
      // the emphasis glides in/out instead of snapping.
      if (!inFocus && hasFocus) {
        const p = focusProgressRef.current
        return {
          ...attrs,
          color: withAlpha(baseAlpha * (1 - 0.8 * p)),
          label: "",
          size: Math.max(HIT_SIZE_MIN, attrs.size * (1 - 0.4 * p)),
        }
      }

      const color = withAlpha(baseAlpha)

      // Hover/selection ring: teal border on focal node only (not all neighbors)
      // — marking all neighbors `highlighted` causes many white plates (sigma
      // renders highlighted nodes through the hover program).
      const isCenter = node === focusCenter

      // Pulse ring: if within active pulse window, boost size slightly
      const pulseEntry = currentPulseMap.get(node)
      let extraSize = 0
      if (pulseEntry !== undefined) {
        const elapsed = Date.now() - pulseEntry
        const t = Math.max(0, 1 - elapsed / PULSE_DURATION_MS)
        extraSize = t * 4  // expands up to 4px at peak
      }

      // Drag lift: +15% size on the actively-dragged node (visible affordance).
      const isDragged = node === currentDragId && currentDragId !== null
      const nodeSize = isDragged ? attrs.size * 1.15 : attrs.size + extraSize

      return {
        ...attrs,
        color,
        size: nodeSize,
        // highlighted only on the hovered/pinned center — not all neighbors
        highlighted: isCenter,
        zIndex: isDragged ? 3 : isPermanentPin ? 2 : isCenter ? 2 : hasFocus && focusNeighbors!.has(node) ? 1 : 0,
        forceLabel: isCenter || isPermanentPin || (hasFocus && focusNeighbors!.has(node)),
      }
    })

    sigma.setSetting("edgeReducer", (edge, attrs) => {
      if (collapsedRef.current) return { ...attrs, hidden: true }
      const currentPinnedId = pinnedIdRef.current
      const hoverId = hoverIdRef.current
      const focusCenter = currentPinnedId ?? hoverId ?? null
      const currentTokens = tokensRef.current

      // Kind-aware base color: preserve the alpha suffix baked at build time.
      // The attrs.color already has the kind-derived alpha suffix appended.
      const baseColor: string = (attrs.color as string) || currentTokens.edge

      // LOD floor: compute once per edge evaluation.
      const minSize = lodEdgeMinSize(lodTierRef.current)
      const sizeBelowFloor = ((attrs.size as number) ?? 1) < minSize

      if (!focusCenter) return { ...attrs, color: baseColor, hidden: sizeBelowFloor }

      // Read the precomputed focus neighbourhood (null when no valid center).
      const focusNeighbors = focusNeighborsRef.current
      if (!focusNeighbors) return { ...attrs, color: baseColor, hidden: sizeBelowFloor }

      const graph = sigma.getGraph()
      const src = graph.source(edge)
      const tgt = graph.target(edge)
      const srcInFocus = focusNeighbors.has(src)
      const tgtInFocus = focusNeighbors.has(tgt)
      if (srcInFocus && tgtInFocus) {
        // Focus neighbourhood edges always stay visible regardless of LOD.
        return { ...attrs, color: baseColor, hidden: false }
      }
      // Non-neighborhood edges recede to dim AND obey the LOD floor —
      // weak non-focus edges drop out when zoomed out.
      return { ...attrs, color: currentTokens.dim, hidden: sizeBelowFloor }
    })

    sigma.refresh()
  }, [sigmaInstance, data])

  // Trigger a cheap refresh whenever lens/filter/tokens/pinnedId/drag changes
  // without reinstalling reducers.
  useEffect(() => {
    if (!sigmaInstance) return
    recomputeFocusNeighbors()
    sigmaInstance.refresh({ skipIndexation: true })
  }, [sigmaInstance, lens, typeFilter, tokens, pinnedId, drilledCommunityId, pulseMap, isDragging, search, config.hideOrphans, recomputeFocusNeighbors])

  // Esc exits a community drill (Model B).
  useEffect(() => {
    if (!drilledCommunityId) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrilledCommunityId(null)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [drilledCommunityId])

  // Animated reveal: briefly reheat the live sim ONLY when the filtered SET
  // changes (search / orphan filter / type filter) so users see what changed.
  // Deliberately excludes pinnedId/isDragging/pulseMap/tokens (those must not
  // re-settle the whole graph — pin drives A4 focus-zoom; drag has its own
  // drop-reheat in handleMouseUp).
  useEffect(() => {
    forceCtrlRef.current?.reheat()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sigmaInstance, search, config.hideOrphans, typeFilter])

  // ---------------------------------------------------------------------------
  // Ingest pulse: fire when newEntityIds changes
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!newEntityIds || newEntityIds.size === 0) return
    const now = Date.now()
    const newEntries: PulseEntry[] = []
    for (const id of newEntityIds) {
      newEntries.push({ nodeId: id, startMs: now })
    }
    pulseEntriesRef.current.push(...newEntries)

    const newMap = new Map<string, number>()
    for (const entry of pulseEntriesRef.current) {
      newMap.set(entry.nodeId, entry.startMs)
    }
    setPulseMap(newMap)

    // Animate out over PULSE_DURATION_MS
    const cleanup = () => {
      const now2 = Date.now()
      pulseEntriesRef.current = pulseEntriesRef.current.filter(
        (e) => now2 - e.startMs < PULSE_DURATION_MS
      )
      if (pulseEntriesRef.current.length > 0) {
        const m = new Map<string, number>()
        for (const e of pulseEntriesRef.current) m.set(e.nodeId, e.startMs)
        setPulseMap(m)
        pulseRafRef.current = requestAnimationFrame(cleanup)
      } else {
        setPulseMap(new Map())
      }
    }
    if (pulseRafRef.current !== null) cancelAnimationFrame(pulseRafRef.current)
    pulseRafRef.current = requestAnimationFrame(cleanup)
  }, [newEntityIds])

  useEffect(() => {
    return () => {
      if (pulseRafRef.current !== null) cancelAnimationFrame(pulseRafRef.current)
      if (focusRafRef.current !== null) cancelAnimationFrame(focusRafRef.current)
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Community hull canvas overlay
  // ---------------------------------------------------------------------------

  // Per-Leiden-level community and super-edge arrays for the multi-level overlay.
  // Must be declared before any early return (useSuperNodeLayer is a hook below).
  const hierarchyQuery = useCommunityHierarchy()
  const levelCommunities = useMemo(
    () => buildLevelCommunities(data?.communities ?? [], hierarchyQuery.data),
    [data, hierarchyQuery.data],
  )
  const levelSuperEdges = useMemo(
    () => buildLevelSuperEdges(
      data?.entities.map((e) => ({ community: e.community ?? null })) ?? [],
      data?.links ?? [],
      hierarchyQuery.data,
    ),
    [data, hierarchyQuery.data],
  )

  const wrappedOnCommunityClick = useCallback((community: CommunityHull) => {
    // Zoom the camera to this community's hull extent before surfacing to parent.
    // For synthetic level-L communities, hull is the descendant-region union —
    // zooming to it crosses into the finer level and reveals sub-communities.
    if (community.hull.length >= 3) {
      focusCameraOnPointsRef.current?.(community.hull)
    }
    onCommunityClick?.(community)
  }, [onCommunityClick])

  // Per-domain nebula: in the "domain" layout, shade domain territories instead
  // of community regions (domains are the meaningful grouping there). Hulls are
  // computed client-side — the server ships community hulls only — and coloured
  // by domain. Other layouts keep the server community regions.
  const domainHulls = useMemo(
    () => (layout === "domain" && data ? buildDomainHulls(data.entities) : []),
    [layout, data],
  )
  const showDomainRegions = layout === "domain" && domainHulls.length > 0
  const activeHulls = showDomainRegions ? domainHulls : data?.communities ?? []
  // Region hulls the double-click-drill hit-tests against (community or domain).
  communitiesRef.current = activeHulls
  const regionColorFor = useMemo(
    () => (showDomainRegions ? (id: string) => domainColor(tokens, id) : undefined),
    [showDomainRegions, tokens],
  )

  useCommunityLayer({
    sigma: sigmaInstance,
    communities: activeHulls,
    tokens,
    hullsVisible: config.hullsVisible,
    onCommunityClick: wrappedOnCommunityClick,
    colorFor: regionColorFor,
  })

  useSuperNodeLayer({
    sigma: sigmaInstance,
    levels: levelCommunities,
    levelSuperEdges,
    tokens,
    enabled: config.collapseCommunities,
    onCommunityClick: wrappedOnCommunityClick,
    onCollapsedChange: (collapsed) => {
      collapsedRef.current = collapsed
      // Pause the live FA2 sim while collapsed — every member node/edge is
      // hidden, so simulating + refreshing the mesh is wasted worker + main-
      // thread work. Reheat when expanding back into members.
      if (collapsed) forceCtrlRef.current?.pause()
      else forceCtrlRef.current?.reheat()
      sigmaRef.current?.refresh({ skipIndexation: true })
    },
  })

  useHighlightEdges({
    sigma: sigmaInstance,
    tokens,
    getFocusCenter: useCallback(
      () => pinnedIdRef.current ?? hoverIdRef.current ?? null,
      [],
    ),
    // Fade the highlight in/out with the same eased focus progress as the node
    // dimming, so hover emphasis is one smooth gesture, not an abrupt flash.
    getFocusProgress: useCallback(() => focusProgressRef.current, []),
  })

  // ---------------------------------------------------------------------------
  // Label density update (no full rebuild)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!sigmaInstance) return
    sigmaInstance.setSetting("labelDensity", LABEL_DENSITY_VALUES[config.labelDensity])
    sigmaInstance.refresh()
  }, [sigmaInstance, config.labelDensity])

  // ---------------------------------------------------------------------------
  // Derived state for the pinned entity card
  // ---------------------------------------------------------------------------
  const pinnedEntity = pinnedId && data
    ? data.entities.find((e) => e.id === pinnedId)
    : undefined

  // ---------------------------------------------------------------------------
  // Camera helpers for semantic zoom drill-down
  // ---------------------------------------------------------------------------

  /** Ease the camera to (re-center on) a focal node + its neighbours. */
  const focusCameraOn = useCallback((nodeId: string) => {
    const sigma = sigmaRef.current
    if (!sigma || !sigma.getGraph().hasNode(nodeId)) return
    const graph = sigma.getGraph()
    const ids: string[] = [nodeId]
    graph.forEachNeighbor(nodeId, (n) => ids.push(n))
    // The camera operates in framed-graph coordinates; getNodeDisplayData
    // returns node positions in that exact space, so the bbox center of the
    // display coords is a valid camera (x,y) target — true re-centering, not
    // just a ratio change.
    const framed: [number, number][] = []
    for (const id of ids) {
      const dd = sigma.getNodeDisplayData(id)
      if (dd) framed.push([dd.x, dd.y])
    }
    const target = cameraTargetForPoints(framed)
    if (!target) return
    const camera = sigma.getCamera()
    if (reducedMotion) camera.setState(target)
    else camera.animate(target, { duration: 400 })
  }, [reducedMotion])
  // Keep ref current so the sigma rebuild closure can call it.
  focusCameraOnRef.current = focusCameraOn

  /** Ease the camera to (re-center on) a set of GRAPH-space points (hull zoom). */
  const focusCameraOnPoints = useCallback((pts: [number, number][]) => {
    const sigma = sigmaRef.current
    if (!sigma || pts.length === 0) return
    // Hull points are graph-space; convert to framed-graph coords (the camera's
    // space) via graph→viewport→framed so the camera can re-center on them.
    const framed: [number, number][] = pts.map(([x, y]) => {
      const f = sigma.viewportToFramedGraph(sigma.graphToViewport({ x, y }))
      return [f.x, f.y] as [number, number]
    })
    const target = cameraTargetForPoints(framed)
    if (!target) return
    const camera = sigma.getCamera()
    if (reducedMotion) camera.setState(target)
    else camera.animate(target, { duration: 400 })
  }, [reducedMotion])
  focusCameraOnPointsRef.current = focusCameraOnPoints

  const handleClearPin = useCallback(() => {
    setPinnedId(null)
    const sigma = sigmaRef.current
    if (!sigma) return
    const camera = sigma.getCamera()
    if (reducedMotion) {
      camera.setState({ x: 0.5, y: 0.5, ratio: 1 })
    } else {
      camera.animatedReset()
    }
  }, [reducedMotion])

  const handleOpenInWiki = useCallback(() => {
    if (!pinnedId) return
    goTo("subjects", { mode: "wiki", entity: pinnedId })
  }, [pinnedId, goTo])

  // Reset view — refit the whole graph and clear focus. The recovery hatch when
  // the user has zoomed/panned into a dead end (camera bounds keep it in range;
  // this re-centres and fits).
  const handleResetView = useCallback(() => {
    setPinnedId(null)
    const sigma = sigmaRef.current
    if (!sigma) return
    const camera = sigma.getCamera()
    if (reducedMotion) camera.setState({ x: 0.5, y: 0.5, ratio: 1, angle: 0 })
    else camera.animatedReset()
  }, [reducedMotion])

  // ---------------------------------------------------------------------------
  // Trust lens empty-state: detect when ALL nodes lack real trust data
  // ---------------------------------------------------------------------------
  const allTrustUnknown = !!(
    data &&
    data.entities.length > 0 &&
    lens === "trust" &&
    data.entities.every((e) => !e.trust_state || e.trust_state === "unknown")
  )

  // ---------------------------------------------------------------------------
  // Entity lookup map — must be declared before any early return (rules-of-hooks)
  // ---------------------------------------------------------------------------
  const entityById = useMemo(
    () => new Map(data?.entities.map((e) => [e.id, e]) ?? []),
    [data],
  )

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------
  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading knowledge map…
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {errorMessage ?? "Failed to load knowledge map."}
        </div>
      </div>
    )
  }

  if (!data || data.entities.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-12">
        <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
          <h2 className="text-lg font-semibold text-foreground">No map data yet</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            The Cartographer map computes after your first ingestion. Ingest a
            document and the layout recomputes within a few minutes — or run
            &ldquo;Graph Map compute&rdquo; from Settings → Diagnostics → Scheduled Jobs.
          </p>
        </div>
      </div>
    )
  }

  const hoveredEntity = tooltipState
    ? data.entities.find((e) => e.id === tooltipState.id)
    : undefined

  function getNodeInfo(entityId: string): { degree: number; topNeighbors: Array<{ id: string; name: string; kind: string }> } {
    const graph = sigmaRef.current?.getGraph()
    if (!graph || !graph.hasNode(entityId)) {
      const entity = entityById.get(entityId)
      const deg = entity ? (graph?.getNodeAttribute(entityId, "_degree") as number | undefined) ?? 0 : 0
      return { degree: deg, topNeighbors: [] }
    }
    const degree = graph.degree(entityId)
    const topNeighbors: Array<{ id: string; name: string; kind: string }> = []
    graph.forEachNeighbor(entityId, (nbId) => {
      if (topNeighbors.length >= 3) return
      const e = entityById.get(nbId)
      if (!e) return
      // Derive edge kind from the stored _kind attribute on the first edge
      let kind = "co_mention"
      graph.forEachEdge(entityId, nbId, (_key, attrs) => {
        kind = (attrs._kind as string | undefined) ?? "co_mention"
      })
      topNeighbors.push({ id: nbId, name: e.name, kind })
    })
    return { degree, topNeighbors }
  }

  return (
    <div
      className="relative h-full w-full bg-background"
      style={{ cursor: isDragging ? "grabbing" : undefined }} // drift-allowed: runtime interaction state
      role="application"
      aria-roledescription="2D knowledge map"
      aria-label={`Cartographer map of ${data.count} entities`}
    >
      {/* Sigma canvas container */}
      <div ref={containerRef} className="h-full w-full" aria-hidden="true" />

      {/* Layout fallback notice — shown when requested layout not yet computed */}
      {layoutFallback && (
        <div className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md bg-amber-500/10 px-3 py-1.5 text-label-xs text-amber-700 dark:text-amber-400 backdrop-blur">
          Wells layout not computed yet — showing force
        </div>
      )}

      {/* Drill-down breadcrumb (Model B) — isolate a community, Esc/✕ to exit */}
      {drilledCommunityId && (() => {
        const drilled = data.communities?.find((c) => String(c.id) === drilledCommunityId)
        return (
          <div className="absolute left-1/2 top-3 z-40 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border/60 bg-card/95 px-3 py-1.5 text-label-xs text-foreground shadow-lg backdrop-blur">
            <span className="max-w-[18rem] truncate">
              Drilled into <span className="font-semibold">{drilled?.label ?? "community"}</span>
            </span>
            <button
              type="button"
              onClick={() => setDrilledCommunityId(null)}
              className="rounded-full border border-border/60 px-1.5 py-0.5 text-muted-foreground hover:bg-accent/40 hover:text-foreground"
              aria-label="Exit drill-down"
            >
              Esc ✕
            </button>
          </div>
        )
      })()}

      {/* Hover tooltip */}
      {tooltipState && hoveredEntity && (() => {
        const { degree, topNeighbors } = getNodeInfo(hoveredEntity.id)
        return (
          <div
            role="tooltip"
            aria-label={`Entity details: ${hoveredEntity.name}`}
            className="pointer-events-none fixed z-50 w-64 rounded-lg border border-border/60 bg-card/95 px-3 py-2 shadow-xl backdrop-blur"
            // Runtime-derived position — drift-allowlisted inline style (popover absolute positioning)
            style={{ left: tooltipState.x + 14, top: tooltipState.y + 14 }} // drift-allowed: runtime pointer position
          >
            <div className="truncate text-sm font-semibold text-foreground">
              {hoveredEntity.name}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-label-xs text-muted-foreground">
              <span className="rounded bg-accent/50 px-1 uppercase">{hoveredEntity.type}</span>
              <span>{hoveredEntity.mention_count} mentions</span>
            </div>
            <div className="mt-1 flex flex-col gap-1">
              <div className="flex items-center gap-1.5 text-label-xs text-muted-foreground">
                <Link2 className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span data-testid="carto-tooltip-degree">{degree} {degree === 1 ? "connection" : "connections"}</span>
              </div>
              {hoveredEntity.trust_state !== "unknown" && (
                <div className="flex items-center gap-1.5 text-label-xs text-muted-foreground">
                  <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden="true" />
                  <span data-testid="carto-tooltip-trust">{hoveredEntity.trust_state}</span>
                  <span aria-hidden="true" className="text-muted-foreground/40">·</span>
                  <span className="text-muted-foreground/60">verified / partial / unverified / contradicted</span>
                </div>
              )}
              {topNeighbors.length > 0 && (
                <div>
                  <div className="mb-0.5 flex items-center gap-1 text-label-xs text-muted-foreground/70">
                    <Users className="h-3 w-3 shrink-0" aria-hidden="true" />
                    <span>Top neighbors</span>
                  </div>
                  <ul className="flex flex-col gap-0.5" aria-label="Top neighbors">
                    {topNeighbors.map((nb) => (
                      <li key={nb.id} className="flex items-center gap-1.5 text-label-xs text-foreground/70">
                        <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/40" aria-hidden="true" />
                        <span className="truncate">{nb.name}</span>
                        <span className="shrink-0 text-muted-foreground/40">{nb.kind === "similar" ? "≈" : "·"}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="mt-1 text-label-xs text-muted-foreground/70">Click to inspect</div>
          </div>
        )
      })()}

      {/* Pinned entity card */}
      {pinnedId && pinnedEntity && (() => {
        const { degree, topNeighbors } = getNodeInfo(pinnedEntity.id)
        return (
          <div
            role="dialog"
            aria-label={`Entity details: ${pinnedEntity.name}`}
            className="absolute bottom-3 left-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {pinnedEntity.name}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-label-xs text-muted-foreground">
                  <span className="rounded bg-accent/50 px-1 uppercase">{pinnedEntity.type}</span>
                  <span>{pinnedEntity.mention_count} mentions</span>
                </div>
              </div>
              <button
                type="button"
                onClick={handleClearPin}
                aria-label="Clear focus"
                className="rounded p-1 text-muted-foreground hover:bg-accent/40"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
            <div className="mt-2 flex flex-col gap-1">
              <div className="flex items-center gap-1.5 text-label-xs text-muted-foreground">
                <Link2 className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span data-testid="carto-pin-degree">{degree} {degree === 1 ? "connection" : "connections"}</span>
              </div>
              {pinnedEntity.trust_state !== "unknown" && (
                <div className="flex items-center gap-1.5 text-label-xs text-muted-foreground">
                  <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden="true" />
                  <span data-testid="carto-pin-trust">{pinnedEntity.trust_state}</span>
                  <span aria-hidden="true" className="text-muted-foreground/40">·</span>
                  <span className="text-muted-foreground/60">verified / partial / unverified / contradicted</span>
                </div>
              )}
              {topNeighbors.length > 0 && (
                <div>
                  <div className="mb-0.5 flex items-center gap-1 text-label-xs text-muted-foreground/70">
                    <Users className="h-3 w-3 shrink-0" aria-hidden="true" />
                    <span>Top neighbors</span>
                  </div>
                  <ul className="flex flex-col gap-0.5" aria-label="Top neighbors">
                    {topNeighbors.map((nb) => (
                      <li key={nb.id} className="flex items-center gap-1.5 text-label-xs text-foreground/70">
                        <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/40" aria-hidden="true" />
                        <span className="truncate">{nb.name}</span>
                        <span className="shrink-0 text-muted-foreground/40">{nb.kind === "similar" ? "≈" : "·"}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={handleOpenInWiki}
              className="mt-2 w-full rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
            >
              Open in Wiki
            </button>
          </div>
        )
      })()}

      {/* Stats overlay */}
      <div className="pointer-events-none absolute right-3 top-3 rounded-md bg-card/80 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
        {data.count} entities · {(data.links ?? []).length.toLocaleString()} connections
        {data.cached && " · cached"}
        {data.silhouette !== null &&
          ` · silhouette ${data.silhouette.toFixed(2)}`}
      </div>

      {/* Reset view — recovery hatch (re-fit + clear focus) */}
      <button
        type="button"
        onClick={handleResetView}
        aria-label="Reset view"
        title="Reset view (fit graph)"
        className="absolute right-3 top-11 flex items-center gap-1 rounded-md border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40 hover:text-foreground"
      >
        <Maximize2 className="h-3 w-3" aria-hidden="true" />
        Reset view
      </button>

      {/* Trust lens empty-state notice */}
      {allTrustUnknown && (
        <div className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md bg-card/80 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
          Trust lens: no verification data yet
        </div>
      )}

      {/* Apply filter indicator */}
      {filter && (
        <div className="pointer-events-none absolute left-3 bottom-3 rounded-md bg-card/80 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
          Filtered: {filter}
        </div>
      )}
    </div>
  )
}
