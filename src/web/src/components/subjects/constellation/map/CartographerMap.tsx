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
import { Loader2, X, Link2, Users, ShieldCheck } from "lucide-react"
import type { GraphMapResponse, CommunityHull } from "@/lib/api/graph-map"
import type { MapConfig } from "./map-config"
import { LABEL_DENSITY_VALUES } from "./map-config"
import { useCommunityLayer, resolveMapTokens, type MapTokens } from "./community-layer"
import { makeDrawNodeHover } from "@/lib/graph/draw-node-hover"
import { HOVER_INTENT_DELAY_MS } from "@/lib/graph/hover-intent"
import { useNavigation } from "@/contexts/navigation-context"
import { domainColor } from "@/lib/graph/identity"
import { createHealController } from "@/lib/graph/interactions/drag-heal"
import { nodeBaseAlpha, ISOLATED_COMMUNITY_ID } from "../palette"
import type { OnInspect, OnFocusEntity } from "@/lib/graph/cycle4-contracts"
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
  /** layout_fallback: true → show inline "wells layout not computed yet" notice */
  layoutFallback?: boolean
  /**
   * Called when the user Shift-drops a node to permanently pin it.
   * Parent can persist this into the saved view's pinnedNodes field.
   */
  onPinnedNodesChange?: (pinnedNodes: Record<string, { x: number; y: number }>) => void
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
  layoutFallback,
  onPinnedNodesChange,
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
      graph.addNode(e.id, {
        x: e.x,
        y: e.y,
        label: e.name,
        size: nodeRadius(deg),
        color: lensColor(e, lens, tokens),
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
    const CO_MENTION_ALPHA_SUFFIX = "38"  // ≈ 22% — primary signal
    const SIMILAR_ALPHA_SUFFIX = "28"     // ≈ 16% — secondary/dimmer

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
        // Click: pin/inspect only per unified click contract.
        setPinnedId((prev) => (prev === node ? null : node))
        onInspectRef.current?.(node)
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
      setPinnedId((prev) => (prev === node ? null : node))
      onInspectRef.current?.(node)
    })

    sigma.on("enterNode", ({ node, event }) => {
      hoverIdRef.current = node
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
      sigma.refresh({ skipIndexation: true })
      setTooltipState(null)
    })

    return () => {
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
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const sigma = sigmaRef.current
    if (!sigma || !data) return
    const graph = sigma.getGraph()
    let moved = false
    for (const e of data.entities) {
      if (graph.hasNode(e.id)) {
        graph.setNodeAttribute(e.id, "x", e.x)
        graph.setNodeAttribute(e.id, "y", e.y)
        moved = true
      }
    }
    if (moved) sigma.refresh({ skipIndexation: false })
  }, [data])

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
      const entity = entityByIdMap.get(node)
      if (!entity) return { ...attrs, hidden: true }

      const currentTokens = tokensRef.current
      const currentLens = lensRef.current
      const currentTypeFilter = typeFilterRef.current
      const currentPinnedId = pinnedIdRef.current
      const hoverId = hoverIdRef.current
      const currentPulseMap = pulseMapRef.current
      const currentDragId = dragRef.current.nodeId
      const isPermanentPin = node in pinnedNodesRef.current

      // Build neighbor sets for focus dimming
      const graph = sigma.getGraph()
      const focusCenter = currentPinnedId ?? hoverId ?? null
      let focusNeighbors: Set<string> | null = null
      if (focusCenter && graph.hasNode(focusCenter)) {
        focusNeighbors = new Set<string>()
        graph.forEachNeighbor(focusCenter, (n) => focusNeighbors!.add(n))
        focusNeighbors.add(focusCenter)
      }

      // Type filter dim
      const typeFiltered = currentTypeFilter.size > 0 && !currentTypeFilter.has(entity.type)

      // Focus dim: when there's a focus center, non-neighbors fade to dim token
      const hasFocus = focusNeighbors !== null
      const inFocus = !hasFocus || focusNeighbors!.has(node)

      if (typeFiltered || (!inFocus && hasFocus)) {
        return {
          ...attrs,
          color: currentTokens.dim,
          label: "",
          // Enforce minimum interactive hit size even when dimmed.
          // 0.6× makes the local subgraph pop — non-focus nodes recede
          // decisively without vanishing (floor = HIT_SIZE_MIN).
          size: Math.max(HIT_SIZE_MIN, attrs.size * 0.6),
        }
      }

      // Apply lens recoloring
      const baseColor = lensColor(entity, currentLens, currentTokens)

      // Confidence/recency → fill alpha. mention_count is the most robust
      // per-node signal available: single-mention entities are newly-observed
      // or rarely-cited and render softer; well-established nodes are opaque.
      // This base alpha COMPOSES with focus-dim and type-filter above (both
      // of which return early before reaching this path).
      const alpha = nodeBaseAlpha(entity.mention_count ?? 1)
      // resolveMapTokens always returns #RRGGBB; append a 2-hex alpha suffix.
      const alphaSuffix = Math.round(alpha * 255).toString(16).padStart(2, "0")
      const color = baseColor.startsWith("#") && baseColor.length === 7
        ? baseColor + alphaSuffix
        : baseColor

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
      const currentPinnedId = pinnedIdRef.current
      const hoverId = hoverIdRef.current
      const focusCenter = currentPinnedId ?? hoverId ?? null
      const currentTokens = tokensRef.current

      // Kind-aware base color: preserve the alpha suffix baked at build time.
      // The attrs.color already has the kind-derived alpha suffix appended.
      const baseColor: string = (attrs.color as string) || currentTokens.edge

      if (!focusCenter) return { ...attrs, color: baseColor }

      const graph = sigma.getGraph()
      if (!graph.hasNode(focusCenter)) return { ...attrs, color: baseColor }

      const focusNeighbors = new Set<string>()
      graph.forEachNeighbor(focusCenter, (n) => focusNeighbors.add(n))
      focusNeighbors.add(focusCenter)

      const src = graph.source(edge)
      const tgt = graph.target(edge)
      const srcInFocus = focusNeighbors.has(src)
      const tgtInFocus = focusNeighbors.has(tgt)
      if (srcInFocus && tgtInFocus) {
        return { ...attrs, color: baseColor, hidden: false }
      }
      // Non-neighborhood edges stay visible but recede to the dim token
      // instead of vanishing — soft de-emphasis, not a blackout of the
      // whole edge layer.
      return { ...attrs, color: currentTokens.dim, hidden: false }
    })

    sigma.refresh()
  }, [sigmaInstance, data])

  // Trigger a cheap refresh whenever lens/filter/tokens/pinnedId/drag changes
  // without reinstalling reducers.
  useEffect(() => {
    if (!sigmaInstance) return
    sigmaInstance.refresh({ skipIndexation: true })
  }, [sigmaInstance, lens, typeFilter, tokens, pinnedId, pulseMap, isDragging])

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
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Community hull canvas overlay
  // ---------------------------------------------------------------------------
  useCommunityLayer({
    sigma: sigmaInstance,
    communities: data?.communities ?? [],
    tokens,
    hullsVisible: config.hullsVisible,
    onCommunityClick,
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

  const handleClearPin = useCallback(() => setPinnedId(null), [])

  const handleOpenInWiki = useCallback(() => {
    if (!pinnedId) return
    goTo("subjects", { mode: "wiki", entity: pinnedId })
  }, [pinnedId, goTo])

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
