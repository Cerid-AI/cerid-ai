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

import { useCallback, useEffect, useRef, useState } from "react"
import Sigma from "sigma"
import Graph from "graphology"
import { Loader2 } from "lucide-react"
import type { GraphMapResponse, CommunityHull } from "@/lib/api/graph-map"
import type { MapConfig } from "./map-config"
import { LABEL_DENSITY_VALUES } from "./map-config"
import { useCommunityLayer, resolveMapTokens, type MapTokens } from "./community-layer"

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
  if (!communityId) return tokens.clusterOther
  return tokens.clusters[hashToSlot(communityId)] ?? tokens.clusterOther
}

// ---------------------------------------------------------------------------
// Lens coloring
// ---------------------------------------------------------------------------

type ColorLens = "cluster" | "trust" | "type"

const TYPE_SLOT: Record<string, number> = {
  PERSON: 0, Person: 0,
  ORG: 1, Organization: 1,
  LOC: 2, Location: 2,
  EVENT: 3, Event: 3,
  ASSET: 4, Asset: 4,
}

function lensColor(
  entity: { type: string; community: string | null; trust_state: string },
  lens: ColorLens,
  tokens: MapTokens,
): string {
  if (lens === "type") {
    const slot = TYPE_SLOT[entity.type]
    return slot !== undefined ? (tokens.clusters[slot] ?? tokens.clusterOther) : tokens.clusterOther
  }
  if (lens === "trust") {
    // Trust lens: map to cluster slots 0(verified), 2(partial), 4(other)
    // so node fill reflects trust grade using the muted categorical ramp
    if (entity.trust_state === "verified") return tokens.clusters[0] ?? tokens.clusterOther
    if (entity.trust_state === "partial") return tokens.clusters[2] ?? tokens.clusterOther
    return tokens.clusterOther
  }
  // cluster (default)
  return clusterColor(entity.community, tokens)
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
  onNodeClick?: (entityId: string) => void
  onCommunityClick?: (community: CommunityHull) => void
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
  onNodeClick,
  onCommunityClick,
}: CartographerMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const onNodeClickRef = useRef(onNodeClick)
  onNodeClickRef.current = onNodeClick

  const [sigmaInstance, setSigmaInstance] = useState<Sigma | null>(null)
  const [tokens, setTokens] = useState<MapTokens>(() => {
    if (typeof document !== "undefined") {
      return resolveMapTokens(document.documentElement)
    }
    // SSR/test fallback — document not available; real tokens read via getComputedStyle
    return {
      clusters: Array(8).fill("#999"), // drift-allowed: SSR fallback only, never reaches browser
      clusterOther: "#999", // drift-allowed: SSR fallback only, never reaches browser
      edge: "#ccc", // drift-allowed: SSR fallback only, never reaches browser
      dim: "#eee", // drift-allowed: SSR fallback only, never reaches browser
      interaction: "#00c8b4", // drift-allowed: SSR fallback only, never reaches browser
      foreground: "#111", // drift-allowed: SSR fallback only, never reaches browser
      background: "#f5f5f5", // drift-allowed: SSR fallback only, never reaches browser
      trustVerified: "#333", // drift-allowed: SSR fallback only, never reaches browser
      trustPartial: "#555", // drift-allowed: SSR fallback only, never reaches browser
      trustUnverified: "#888", // drift-allowed: SSR fallback only, never reaches browser
      grid: "#eee", // drift-allowed: SSR fallback only, never reaches browser
    }
  })

  // Pinned entity card state
  const [pinnedId, setPinnedId] = useState<string | null>(null)
  const [hoverState, setHoverState] = useState<{ id: string; x: number; y: number } | null>(null)

  // Pulse ring state: map from nodeId → start timestamp
  const [pulseMap, setPulseMap] = useState<Map<string, number>>(new Map())
  const pulseRafRef = useRef<number | null>(null)
  const pulseEntriesRef = useRef<PulseEntry[]>([])

  // Re-read tokens when theme changes (watch for .dark class toggle)
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTokens(resolveMapTokens(document.documentElement))
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

    // Edge budget: sort by weight descending, apply cap
    const allLinks = [...data.links]
    allLinks.sort((a, b) => b[2] - a[2])
    let budget: number
    if (config.edgeBudget === "off") budget = 0
    else if (config.edgeBudget === "2k") budget = 2000
    else if (config.edgeBudget === "all") budget = allLinks.length
    else budget = 8000  // "8k" default

    const links = allLinks.slice(0, budget)

    for (const [si, ti, weight] of links) {
      const src = data.entities[si]
      const tgt = data.entities[ti]
      if (!src || !tgt) continue
      if (!graph.hasNode(src.id) || !graph.hasNode(tgt.id)) continue
      const key = `${src.id}::${tgt.id}`
      if (graph.hasEdge(key)) continue
      graph.addEdgeWithKey(key, src.id, tgt.id, {
        size: edgeWidth(weight),
        color: tokens.edge,
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
    })

    sigmaRef.current = sigma
    setSigmaInstance(sigma)

    sigma.on("clickNode", ({ node }) => {
      setPinnedId((prev) => (prev === node ? null : node))
      onNodeClickRef.current?.(node)
    })

    sigma.on("enterNode", ({ node, event }) => {
      const orig = event.original
      const clientX = "clientX" in orig ? (orig as MouseEvent).clientX : 0
      const clientY = "clientY" in orig ? (orig as MouseEvent).clientY : 0
      setHoverState({ id: node, x: clientX, y: clientY })
    })
    sigma.on("leaveNode", () => setHoverState(null))
    sigma.on("moveBody", () => setHoverState(null))

    return () => {
      sigmaRef.current?.kill()
      sigmaRef.current = null
    }
    // Only rebuild when data or core config changes.
    // Lens/filter changes are handled by the reducer effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, config.edgeBudget, tokens])

  // ---------------------------------------------------------------------------
  // Node/edge reducers for lens + filter + hover dimming (run without rebuild)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!sigmaInstance || !data) return

    const sigma = sigmaInstance
    const graph = sigma.getGraph()

    // Rebuild degree map (quick since we have data in scope)
    const degreeMap = new Map<number, number>()
    for (const [s, t] of data.links) {
      degreeMap.set(s, (degreeMap.get(s) ?? 0) + 1)
      degreeMap.set(t, (degreeMap.get(t) ?? 0) + 1)
    }

    // Build neighbor set for the pinned node for dimming
    const pinnedNeighbors = new Set<string>()
    if (pinnedId && graph.hasNode(pinnedId)) {
      graph.forEachNeighbor(pinnedId, (n) => pinnedNeighbors.add(n))
      pinnedNeighbors.add(pinnedId)
    }

    const hoverNeighbors = new Set<string>()
    if (hoverState?.id && graph.hasNode(hoverState.id)) {
      graph.forEachNeighbor(hoverState.id, (n) => hoverNeighbors.add(n))
      hoverNeighbors.add(hoverState.id)
    }

    const focusNeighbors = pinnedId ? pinnedNeighbors : hoverState?.id ? hoverNeighbors : null
    const focusCenter = pinnedId ?? hoverState?.id ?? null

    // Build index for fast entity lookup
    const entityByIdMap = new Map(data.entities.map((e) => [e.id, e]))

    sigma.setSetting("nodeReducer", (node, attrs) => {
      const entity = entityByIdMap.get(node)
      if (!entity) return { ...attrs, hidden: true }

      // Type filter dim
      const typeFiltered = typeFilter.size > 0 && !typeFilter.has(entity.type)

      // Focus dim: when there's a focus center, non-neighbors fade to dim token
      const hasFocus = focusNeighbors !== null
      const inFocus = !hasFocus || focusNeighbors.has(node)

      if (typeFiltered || (!inFocus && hasFocus)) {
        return {
          ...attrs,
          color: tokens.dim,
          label: "",
          size: attrs.size * 0.7,
        }
      }

      // Apply lens recoloring
      const color = lensColor(entity, lens, tokens)

      // Hover/selection ring: teal border on focal node and its 1-hop
      const isCenter = node === focusCenter
      const isNeighbor = hasFocus && focusNeighbors.has(node) && !isCenter

      // Pulse ring: if within active pulse window, boost size slightly
      const pulseEntry = pulseMap.get(node)
      let extraSize = 0
      if (pulseEntry !== undefined) {
        const elapsed = Date.now() - pulseEntry
        const t = Math.max(0, 1 - elapsed / PULSE_DURATION_MS)
        extraSize = t * 4  // expands up to 4px at peak
      }

      return {
        ...attrs,
        color,
        size: attrs.size + extraSize,
        // Use sigma's built-in highlighted for teal ring (rendered via NodeCircleProgram border)
        highlighted: isCenter || isNeighbor,
        zIndex: isCenter ? 2 : isNeighbor ? 1 : 0,
        forceLabel: isCenter || (hasFocus && isNeighbor),
      }
    })

    sigma.setSetting("edgeReducer", (edge, attrs) => {
      if (focusNeighbors === null) {
        return { ...attrs, color: tokens.edge }
      }
      const src = sigmaInstance.getGraph().source(edge)
      const tgt = sigmaInstance.getGraph().target(edge)
      const srcInFocus = focusNeighbors.has(src)
      const tgtInFocus = focusNeighbors.has(tgt)
      if (srcInFocus && tgtInFocus) {
        // Ego edge: slightly brighter
        return { ...attrs, color: tokens.edge, hidden: false }
      }
      return { ...attrs, hidden: true }
    })

    sigma.refresh()
  }, [sigmaInstance, data, lens, typeFilter, tokens, pinnedId, hoverState, pulseMap])

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
    if (pinnedId) onNodeClickRef.current?.(pinnedId)
  }, [pinnedId])

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

  const hoveredEntity = hoverState
    ? data.entities.find((e) => e.id === hoverState.id)
    : undefined

  return (
    <div
      className="relative h-full w-full bg-background"
      role="application"
      aria-roledescription="2D knowledge map"
      aria-label={`Cartographer map of ${data.count} entities`}
    >
      {/* Sigma canvas container */}
      <div ref={containerRef} className="h-full w-full" aria-hidden="true" />

      {/* Hover tooltip */}
      {hoverState && hoveredEntity && (
        <div
          className="pointer-events-none fixed z-50 max-w-xs rounded-lg border border-border/60 bg-card/95 px-3 py-2 shadow-xl backdrop-blur"
          // Runtime-derived position — drift-allowlisted inline style (popover absolute positioning)
          style={{ left: hoverState.x + 14, top: hoverState.y + 14 }} // drift-allowed: runtime pointer position
        >
          <div className="truncate text-sm font-semibold text-foreground">
            {hoveredEntity.name}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-label-xs text-muted-foreground">
            <span className="uppercase">{hoveredEntity.type}</span>
            <span>·</span>
            <span>{hoveredEntity.mention_count} mentions</span>
            {hoveredEntity.trust_state !== "unknown" && (
              <>
                <span>·</span>
                <span>{hoveredEntity.trust_state}</span>
              </>
            )}
          </div>
          <div className="mt-1 text-label-xs text-muted-foreground/70">Click to pin</div>
        </div>
      )}

      {/* Pinned entity card */}
      {pinnedId && pinnedEntity && (
        <div className="absolute bottom-3 left-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">
                {pinnedEntity.name}
              </div>
              <div className="mt-0.5 text-label-xs text-muted-foreground">
                <span className="uppercase">{pinnedEntity.type}</span>
                {" · "}
                {pinnedEntity.mention_count} mentions
                {pinnedEntity.trust_state !== "unknown" &&
                  ` · ${pinnedEntity.trust_state}`}
              </div>
            </div>
            <button
              type="button"
              onClick={handleClearPin}
              aria-label="Clear focus"
              className="rounded p-1 text-muted-foreground hover:bg-accent/40"
            >
              ✕
            </button>
          </div>
          <button
            type="button"
            onClick={handleOpenInWiki}
            className="mt-2 w-full rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
          >
            Open in Wiki
          </button>
        </div>
      )}

      {/* Stats overlay */}
      <div className="pointer-events-none absolute right-3 top-3 rounded-md bg-card/80 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
        {data.count} entities · {(data.links ?? []).length.toLocaleString()} connections
        {data.cached && " · cached"}
        {data.silhouette !== null &&
          ` · silhouette ${data.silhouette.toFixed(2)}`}
      </div>

      {/* Apply filter indicator */}
      {filter && (
        <div className="pointer-events-none absolute left-3 bottom-3 rounded-md bg-card/80 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
          Filtered: {filter}
        </div>
      )}
    </div>
  )
}
