// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Constellation — dual-mode knowledge-graph view.
//   "map" (default): flat 2D Cartographer map (sigma.js v3, no physics)
//   "3d" (retained): the existing React Three Fiber cinematic scene
//
// View mode persists in localStorage "cerid-constellation-mode", default "map".
// Both modes share the same server x/y layout so the mental map is stable.

import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ComponentRef } from "react"
import { Canvas } from "@react-three/fiber"
import { AdaptiveDpr, OrbitControls, PerformanceMonitor } from "@react-three/drei"
import { useQuery } from "@tanstack/react-query"
import { Loader2, Minimize2, Maximize2, Play, Pause, Shuffle, GitMerge } from "lucide-react"
import { Slider } from "@/components/ui/slider"
import { fetchEmbeddings3D } from "@/lib/api/embeddings-3d"
import type { Vec3 } from "./drag-plane"
import { InstancedNodes } from "./instanced-nodes"
import { NeuralLinks } from "./neural-links"
import { HubLabels } from "./hub-labels"
import { AmbientParticles } from "./ambient-particles"
import { ParallaxStarfield } from "./starfield"
import { NebulaBackdrop } from "./nebula-backdrop"
import { SimilarNeighborsPanel } from "./similar-neighbors-panel"
import { rankSimilarNeighbors } from "./similar-neighbors"
import { TourCameraAnimator, TourControlPanel, useTourState, FocusCameraAnimator, FocusExitSampler } from "./tour-controller"
import { QUALITY_SETTINGS, QUALITY_TIERS, degradeTier, upgradeTier, loadQuality, saveQuality, type QualityTier } from "./quality"
import { communityRgb, trustRgb, typeRgb, domainRgb, nodeBaseAlpha } from "./palette"
import { CartographerMap } from "./map/CartographerMap"
import { useGraphMap } from "./map/use-graph-map"
import { loadMapConfig, saveMapConfig, type MapConfig } from "./map/map-config"
import { Timebar } from "./map/timebar"
import { buildTimeHistogram } from "./map/time-window"
import { StructuralGapsPanel } from "./map/structural-gaps-panel"
import { fetchStructuralGaps, type StructuralGap } from "@/lib/api/graph-structural-gaps"
import { useCommunityHierarchy } from "./map/use-community-hierarchy"
import { buildAncestorIndex } from "./map/community-hierarchy-levels"
import { buildSuperNodes3D } from "./supernodes-3d"
import { CollapseLOD, SuperNodes3D } from "./supernodes-layer"
import { boundingSphere, framingDistanceFor } from "./camera-focus-3d"
import type { CommunityHull } from "@/lib/api/graph-map"
import { useNavigation } from "@/contexts/navigation-context"
import { useTheme } from "@/hooks/use-theme"
import { resolveMapTokens, type MapTokens } from "./map/community-layer"
import type { MapLayoutV2 as MapLayout } from "@/lib/graph/cycle4-contracts"
import { TRUST_HALO_HEX, SURFACE_HEX } from "@/theme/shader-tokens"

// ---------------------------------------------------------------------------
// View-mode persistence
// ---------------------------------------------------------------------------

type ViewMode = "map" | "3d" | "live"
const VIEW_MODE_KEY = "cerid-constellation-mode"
const VIEW_MODE_LABEL: Record<ViewMode, string> = { map: "Map", "3d": "3D", live: "Live" }

function loadViewMode(): ViewMode {
  try {
    const stored = localStorage.getItem(VIEW_MODE_KEY)
    if (stored === "map" || stored === "3d" || stored === "live") return stored
  } catch {
    // storage unavailable
  }
  return "map"
}

function saveViewMode(mode: ViewMode): void {
  try {
    localStorage.setItem(VIEW_MODE_KEY, mode)
  } catch {
    // storage unavailable
  }
}

type ColorLens = "cluster" | "trust" | "type" | "domain" | "bridges"
// mapOnly lenses need the 2D graph structure (e.g. betweenness) and aren't
// offered in the 3D toolbar, which has no graphology graph to compute over.
const COLOR_LENSES: { id: ColorLens; label: string; hint: string; mapOnly?: boolean }[] = [
  { id: "cluster", label: "Clusters", hint: "Color by knowledge community" },
  { id: "trust", label: "Trust", hint: "Verification bands: green verified · amber partial · red unverified" },
  { id: "type", label: "Types", hint: "Color by entity type" },
  { id: "domain", label: "Domains", hint: "Color by primary knowledge domain (hash-stable; icon + label identify collisions)" },
  { id: "bridges", label: "Bridges", hint: "Betweenness centrality — highlights the connector entities that bridge otherwise-separate clusters", mapOnly: true },
]

// Postprocessing only loads for Ultra — nobody else pays for the bundle.
const UltraEffects = lazy(() => import("./ultra-effects"))

// cosmos.gl "Live" mode (B8) is its own lazy chunk (vendor-cosmos) — only
// loaded when the user switches to the self-organizing scene.
const CosmosLive = lazy(() => import("./cosmos-live"))

// Community drill-down (B4.4): alpha applied to every entity outside the
// focused community once a super-node is clicked. Reuses the same
// `visibility` channel instanced-nodes.tsx/neural-links.tsx already consume
// for lens/type-filter fading — no new per-entity alpha mechanism.
const FOCUS_FADE_ALPHA = 0.12

export interface ConstellationProps {
  /** Initial focal entity (optional — UMAP shows global view by default) */
  focalEntity?: string
  /** Optional entity-type filter */
  filter?: string | null
  /** Click handler — fires when user clicks a node */
  onNodeClick?: (entityId: string) => void
}

// ---------------------------------------------------------------------------
// MapConfigPanel — small chip-row popover for edge budget / label density / hulls.
// Matches existing overlay chip styling in Constellation.tsx.
// ---------------------------------------------------------------------------

function MapConfigPanel({
  config,
  onChange,
  includeIsolated,
  onIncludeIsolatedChange,
  isolatedCount,
}: {
  config: MapConfig
  onChange: (patch: Partial<MapConfig>) => void
  includeIsolated: boolean
  onIncludeIsolatedChange: (v: boolean) => void
  isolatedCount: number
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Map settings"
        aria-expanded={open}
        className="rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40"
      >
        Map settings
      </button>
      {open && (
        <div className="absolute bottom-full right-0 mb-1 w-52 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
          <div className="flex flex-col gap-2">
            {/* Edge budget */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Edges</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Edge budget">
                {(["off", "2k", "8k", "all"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.edgeBudget === v}
                    onClick={() => onChange({ edgeBudget: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs transition-colors ${
                      config.edgeBudget === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Label density */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Labels</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Label density">
                {(["sparse", "normal", "rich"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.labelDensity === v}
                    onClick={() => onChange({ labelDensity: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs capitalize transition-colors ${
                      config.labelDensity === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Territories mode (A4): nebula = canvas hulls, contours = GPU metaballs */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Regions</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Region rendering">
                {(["off", "nebula", "contours"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.territories === v}
                    onClick={() => onChange({ territories: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs capitalize transition-colors ${
                      config.territories === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Live layout toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.liveLayout}
                onChange={(e) => onChange({ liveLayout: e.target.checked })}
                className="rounded border-border/60"
              />
              Live motion
            </label>

            {/* Hide orphans toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.hideOrphans}
                onChange={(e) => onChange({ hideOrphans: e.target.checked })}
                className="rounded border-border/60"
              />
              Hide orphans
            </label>

            {/* Collapse communities toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.collapseCommunities}
                onChange={(e) => onChange({ collapseCommunities: e.target.checked })}
                className="rounded border-border/60"
              />
              Collapse communities
            </label>

            {/* Show isolated toggle — hidden when count is 0 */}
            {isolatedCount > 0 && (
              <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={includeIsolated}
                  onChange={(e) => onIncludeIsolatedChange(e.target.checked)}
                  className="rounded border-border/60"
                />
                Show isolated ({isolatedCount})
              </label>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component — InstancedMesh-backed (Phase B Day 5). All entities
// share one geometry + material; positions and colors are uploaded to
// the GPU once and stay there until the entity list changes.
// ---------------------------------------------------------------------------

export default function Constellation({ focalEntity, filter, onNodeClick }: ConstellationProps) {
  const { goTo, composeChat } = useNavigation()

  // ---------------------------------------------------------------------------
  // View mode: "map" (Cartographer 2D) | "3d" (R3F scene)
  // ---------------------------------------------------------------------------
  const [viewMode, setViewMode] = useState<ViewMode>(loadViewMode)
  const handleViewMode = useCallback((mode: ViewMode) => {
    saveViewMode(mode)
    setViewMode(mode)
  }, [])

  // ---------------------------------------------------------------------------
  // Map config (edge budget, label density, hulls on/off)
  // ---------------------------------------------------------------------------
  const [mapConfig, setMapConfig] = useState<MapConfig>(loadMapConfig)
  const handleMapConfig = useCallback((patch: Partial<MapConfig>) => {
    setMapConfig((prev) => {
      const next = { ...prev, ...patch }
      saveMapConfig(next)
      return next
    })
  }, [])

  // ---------------------------------------------------------------------------
  // Layout state — drives per-layout query key + presets
  // ---------------------------------------------------------------------------
  const [layout, setLayout] = useState<MapLayout>("force")
  const handleLayout = useCallback((next: MapLayout) => setLayout(next), [])

  // ---------------------------------------------------------------------------
  // Isolated-node toggle — off by default (graph shows connected core)
  // ---------------------------------------------------------------------------
  const [includeIsolated, setIncludeIsolated] = useState(false)

  // Ambient toggle — opt-in glow/neon mode, forced off under reduced-motion
  const [ambientMode, setAmbientMode] = useState(false)

  // ---------------------------------------------------------------------------
  // Cartographer map data (only fetches when in "map" mode to save bandwidth)
  // ---------------------------------------------------------------------------
  const {
    data: mapData,
    isLoading: mapLoading,
    isError: mapError,
    error: mapErrorObj,
    drainNewIds,
  } = useGraphMap(layout, includeIsolated)

  // ---------------------------------------------------------------------------
  // Community card state (shown when a hull/community label is clicked)
  // ---------------------------------------------------------------------------
  const [pinnedCommunity, setPinnedCommunity] = useState<CommunityHull | null>(null)
  // Per-community combos (A10): level-0 community ids collapsed into a disc.
  const [manualCollapsed, setManualCollapsed] = useState<ReadonlySet<string>>(() => new Set())
  // Timebar (A9) window + timelapse (A8) cursor. The cursor advances in
  // discrete steps (not per-frame) so playback never churns React.
  const [timeWindow, setTimeWindow] = useState<[number, number] | null>(null)
  const [playing, setPlaying] = useState(false)
  const [playCursor, setPlayCursor] = useState<number | null>(null)
  const toggleManualCollapsed = useCallback((id: string) => {
    setManualCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Effective time filter read by the map's node reducer: brush window +
  // playback cursor (cursor only while playing).
  const timeFilter = useMemo(
    () =>
      timeWindow || playing
        ? { window: timeWindow, cursor: playing ? playCursor : null }
        : null,
    [timeWindow, playing, playCursor],
  )

  const handleCommunityClick = useCallback((community: CommunityHull) => {
    setPinnedCommunity((prev) => (prev?.id === community.id ? null : community))
  }, [])

  // ---------------------------------------------------------------------------
  // Structural gaps (C2): the advisory panel. Fetches only when open + in map
  // mode. Hovering a gap highlights its two hulls; "Explore in chat" seeds the
  // composer to reason about bridging them.
  // ---------------------------------------------------------------------------
  const [showGaps, setShowGaps] = useState(false)
  const [gapHighlight, setGapHighlight] = useState<ReadonlySet<string> | undefined>(undefined)
  const gapsQuery = useQuery({
    queryKey: ["graph-structural-gaps"],
    queryFn: ({ signal }) => fetchStructuralGaps(8, signal),
    enabled: viewMode === "map" && showGaps,
    staleTime: 10 * 60 * 1000,
  })
  const handleGapHover = useCallback((gap: StructuralGap | null) => {
    setGapHighlight(gap ? new Set([gap.community_a.id, gap.community_b.id]) : undefined)
  }, [])
  const handleGapExplore = useCallback(
    (gap: StructuralGap) => {
      const bridges = gap.bridging_candidates.map((c) => c.name).join(", ")
      composeChat({
        text:
          `My knowledge graph shows "${gap.community_a.label}" and "${gap.community_b.label}" are closely related in meaning ` +
          `but barely connected. How are they linked, and what should I capture to bridge them?` +
          (bridges ? ` Possible bridging entities: ${bridges}.` : ""),
      })
    },
    [composeChat],
  )

  // ---------------------------------------------------------------------------
  // 3D mode: existing R3F data query (still needed when viewMode === "3d")
  // ---------------------------------------------------------------------------
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["constellation-embeddings-3d", focalEntity ?? null, filter ?? null, includeIsolated],
    queryFn: ({ signal }) => fetchEmbeddings3D({ filter, includeIsolated, signal }),
    // The corpus is alive: the on-ingest subscriber + manual Run-now both
    // recompute the projection and bust the server cache. Poll cheaply
    // (Redis cache hit when nothing changed) and keep the previous frame
    // on screen while refetching so growth animates in without a flash.
    staleTime: 60 * 1000,
    refetchInterval: 75 * 1000,
    placeholderData: (prev) => prev,
    // Fetch for the 3D scene and the cosmos "Live" scene (both consume the
    // same embeddings + links payload).
    enabled: viewMode === "3d" || viewMode === "live",
  })

  // ---------------------------------------------------------------------------
  // 3D community collapse (B4.3): same hierarchy endpoint the 2D map's
  // useSuperNodeLayer consumes (map/use-community-hierarchy.ts), gated to 3D
  // mode. collapsedLevel is driven by camera distance (see <CollapseLOD>
  // inside the Canvas below) — null means members are shown.
  // ---------------------------------------------------------------------------
  const hierarchyQuery = useCommunityHierarchy({ enabled: viewMode === "3d" })
  const ancestorIx = useMemo(
    () =>
      hierarchyQuery.data && hierarchyQuery.data.nodes.length > 0
        ? buildAncestorIndex(hierarchyQuery.data)
        : null,
    [hierarchyQuery.data],
  )
  const [collapsedLevel, setCollapsedLevel] = useState<number | null>(null)

  // ---------------------------------------------------------------------------
  // 3D community drill-down (B4.4): click a super-node -> ease the camera to
  // that community's bounding sphere and fade the rest of the corpus via the
  // existing `visibility` alpha channel. `level` pins the Leiden level the
  // clicked super-node was built at (buildSuperNodes3D's own id scheme), so
  // membership resolves consistently even as collapsedLevel keeps changing
  // (CollapseLOD samples camera distance every frame, independent of focus).
  // ---------------------------------------------------------------------------
  const [focus, setFocus] = useState<{ communityId: string; level: number } | null>(null)
  const controlsRef = useRef<ComponentRef<typeof OrbitControls>>(null)

  const handleSuperNodeSelect = useCallback(
    (communityId: string) => {
      if (collapsedLevel === null) return // stray event after supers unmounted
      setFocus({ communityId, level: collapsedLevel })
    },
    [collapsedLevel],
  )

  // Secondary exit path: zooming back out past COLLAPSE_IN re-collapses the
  // graph (CollapseLOD drives collapsedLevel from actual camera distance from
  // the world ORIGIN every frame, unaware of `focus`). The first null->non-null
  // transition *after* a focus began is the signal — collapsedLevel is
  // already non-null the instant a super-node is clicked (that's how it got
  // rendered), so a bare "collapsedLevel !== null" check would clear focus
  // immediately; watching for a transition avoids that.
  //
  // This path is unreliable on its own: it only crosses back through null for
  // communities near the origin. A community centroid far from the origin
  // (the "wells" layout pushes centroids apart) can keep the camera's
  // distance-from-origin >= COLLAPSE_OUT for the whole focus session, so this
  // never fires. <FocusExitSampler> below (focus-RELATIVE — measures distance
  // from the community's own centroid) is the reliable exit path; this one is
  // kept as a cheap fallback for the near-origin case where it still helps.
  const prevCollapsedRef = useRef<number | null>(collapsedLevel)
  useEffect(() => {
    if (focus !== null && prevCollapsedRef.current === null && collapsedLevel !== null) {
      setFocus(null)
    }
    prevCollapsedRef.current = collapsedLevel
  }, [collapsedLevel, focus])

  // Escape key is a guaranteed exit regardless of camera position — good UX,
  // and a fallback in case both camera-distance heuristics above miss.
  useEffect(() => {
    if (focus === null) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFocus(null)
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [focus])

  // Leaving 3D mode drops a stale focus so returning doesn't fade/re-tween
  // toward a community the user never re-selected this visit. It also
  // unmounts <NeuralLinks>/<InstancedNodes> (the whole Canvas swaps out
  // for the 2D map below), so any drag pin must be cleared here too — see
  // the showMembers effect below for the same cleanup on a same-mode
  // collapse-to-supernodes.
  useEffect(() => {
    if (viewMode !== "3d") {
      setFocus(null)
      if (pinnedPositionsRef.current.size > 0) {
        pinnedPositionsRef.current.clear()
        setPinVersion((v) => v + 1)
      }
    }
  }, [viewMode])

  const tour = useTourState()

  // Hover state for the Obsidian-style neighborhood focus + tooltip.
  const [hover, setHover] = useState<{ index: number; x: number; y: number } | null>(null)
  const handleHover = useCallback((index: number | null, clientX?: number, clientY?: number) => {
    if (index === null) {
      setHover(null)
    } else {
      setHover({ index, x: clientX ?? 0, y: clientY ?? 0 })
    }
  }, [])

  // 3D node drag (B2.2): OrbitControls must be suspended while a node is
  // being dragged, so the camera doesn't orbit under the pointer.
  const [dragging, setDragging] = useState(false)
  // Transient per-node drag-pin overrides, threaded down to NeuralLinks so
  // the dragged node's edges follow it. A ref (not state) holds the Map —
  // it's mutated in place on every drag-move; pinVersion is the state bump
  // that tells NeuralLinks' effect a patch is due, without re-rendering the
  // whole scene tree on every pointer-move.
  const pinnedPositionsRef = useRef<Map<string, Vec3>>(new Map())
  const [pinVersion, setPinVersion] = useState(0)
  const handleNodeMoved = useCallback((entityId: string, pos: Vec3) => {
    pinnedPositionsRef.current.set(entityId, pos)
    setPinVersion((v) => v + 1)
  }, [])

  // Adjacency + degree from the link triples — powers neighborhood
  // focus (hover), node sizing (centrality), label ranking, and the
  // tooltip's connection count.
  const neighbors = useMemo(() => {
    const map = new Map<number, Set<number>>()
    for (const [s, t] of data?.links ?? []) {
      if (!map.has(s)) map.set(s, new Set())
      if (!map.has(t)) map.set(t, new Set())
      map.get(s)!.add(t)
      map.get(t)!.add(s)
    }
    return map
  }, [data?.links])

  const degrees = useMemo(() => {
    const arr = new Float32Array(data?.entities.length ?? 0)
    for (const [idx, set] of neighbors) arr[idx] = set.size
    return arr
  }, [neighbors, data?.entities.length])

  // Resolved tokens for the domain lens 3D path (domainRgb needs hex resolved from CSS vars).
  // Lazy-init from DOM; re-resolves on theme change (same pattern as CartographerMap/Atlas).
  const [tokens3D, setTokens3D] = useState<MapTokens>(() =>
    typeof document !== "undefined" ? resolveMapTokens(document.documentElement) : {
      clusters: Array(8).fill("#888888") as string[], // drift-allowed: SSR fallback only
      clusterOther: "#888888", // drift-allowed: SSR fallback only
      domains: Array(12).fill("#888888") as string[], // drift-allowed: SSR fallback only
      domainOther: "#666666", // drift-allowed: SSR fallback only
      edge: "#888888", // drift-allowed: SSR fallback only
      dim: "#888888", // drift-allowed: SSR fallback only
      interaction: "#00C8B4", // drift-allowed: SSR fallback only
      foreground: "#111111", // drift-allowed: SSR fallback only
      background: "#f5f5f5", // drift-allowed: SSR fallback only
      trustVerified: "#555555", // drift-allowed: SSR fallback only
      trustPartial: "#777777", // drift-allowed: SSR fallback only
      trustUnverified: "#999999", // drift-allowed: SSR fallback only
      graphite: "#6b7080", // drift-allowed: SSR fallback only
      grid: "#eeeeee", // drift-allowed: SSR fallback only
    }
  )

  // Re-resolve domain tokens when theme changes (3D mode only — map mode
  // has its own token observer inside CartographerMap/Atlas).
  useEffect(() => {
    if (viewMode !== "3d") return
    const observer = new MutationObserver(() => {
      setTokens3D(resolveMapTokens(document.documentElement))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [viewMode])

  // --- Exploration tools: lens recoloring, type filter, pin-to-focus ---
  const [lens, setLens] = useState<ColorLens>("cluster")
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set())
  const [pinned, setPinned] = useState<number | null>(null)
  const [search, setSearch] = useState("")

  // Top entity types by frequency — shared between map and 3D modes.
  // Map mode uses mapData?.entities; 3D mode uses data?.entities.
  const typeChips = useMemo(() => {
    const counts = new Map<string, number>()
    const source = viewMode === "map" ? (mapData?.entities ?? []) : (data?.entities ?? [])
    for (const e of source) {
      counts.set(e.type, (counts.get(e.type) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)
  }, [viewMode, mapData?.entities, data?.entities])

  // Lens colors: cluster = community palette, trust = verification bands,
  // type = per-type hue, domain = token-routed domain hue (first token-derived
  // 3D color — sets the Cycle-4 precedent for demoting COMMUNITY_PALETTE_RGB).
  // One Float32Array upload, GPU does the rest.
  const nodeColors = useMemo(() => {
    const ents = data?.entities ?? []
    const arr = new Float32Array(ents.length * 3)
    for (let i = 0; i < ents.length; i++) {
      const rgb = lens === "trust"
        ? trustRgb(ents[i].trust_state)
        : lens === "type"
          ? typeRgb(ents[i].type)
          : lens === "domain"
            ? domainRgb(tokens3D, ents[i].primary_domain ?? null)
            : communityRgb(ents[i].community)
      arr[i * 3] = rgb[0]; arr[i * 3 + 1] = rgb[1]; arr[i * 3 + 2] = rgb[2]
    }
    return arr
  // tokens3D is included so theme swaps re-derive colors for the domain lens.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.entities, lens, tokens3D])

  const toggleType = useCallback((t: string) => {
    setTypeFilter((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }, [])

  // Pinned focus wins over transient hover for the neighborhood dimming.
  const focusIndex = pinned ?? hover?.index ?? null
  const pinnedEntity = pinned !== null ? data?.entities[pinned] : undefined

  // kNN neighbors panel (B5): the pinned node's strongest SIMILAR_TO neighbors,
  // ranked client-side from the links already in hand. The pinned node's
  // incident edges (similar included) already brighten via focusIndex → the
  // neural-links aDim channel, so the panel only needs to surface the ranking.
  const similarNeighbors = useMemo(() => {
    if (pinned === null || !data) return []
    return rankSimilarNeighbors(pinned, data.links ?? [], data.entities, 10)
  }, [pinned, data])

  // One-shot camera fly-to a picked neighbor (B5). Fed to a dedicated
  // FocusCameraAnimator; cleared on unpin so it never re-fires.
  const [flyToNode, setFlyToNode] = useState<Vec3 | null>(null)
  useEffect(() => {
    if (pinned === null) setFlyToNode(null)
  }, [pinned])
  const handlePickNeighbor = useCallback(
    (index: number) => {
      const ent = data?.entities[index]
      setPinned(index)
      // A node fly-to supersedes any community drill-down focus.
      setFocus(null)
      if (ent) setFlyToNode([ent.x, ent.y, ent.z])
    },
    [data],
  )

  // Reduced motion collapses growth, pulses, breathing, and auto-rotate.
  const animate = useMemo(
    () => !(typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches),
    [],
  )

  // Resolved theme drives the cinema pass (B1): dark → bloom + vignette;
  // light → ambient occlusion, no bloom. Also gates the dark-only nebula (B4)
  // and picks the label palette (B2). Reactive via useTheme's external store.
  const { theme } = useTheme()
  const isDark = theme === "dark"

  // cosmos.gl "Live" mode controls (B8). Starts frozen under reduced motion.
  const [liveRunning, setLiveRunning] = useState(() => animate)
  const [liveRepulsion, setLiveRepulsion] = useState(1.0)
  const [liveBigBang, setLiveBigBang] = useState(0)

  // Ambient mode = opt-in glow/neon; forced off under reduced-motion (Cycle 4 §5).
  const effectiveAmbient = ambientMode && animate

  // Timelapse playback (A8): step a growth cursor over the entity birth span
  // in discrete frames (~48 over 12s) so nodes appear in creation order.
  // Discrete steps, not per-frame rAF, keep React churn negligible.
  const togglePlay = useCallback(() => setPlaying((p) => !p), [])
  useEffect(() => {
    if (!playing) {
      setPlayCursor(null)
      return
    }
    const hist = buildTimeHistogram(mapData?.entities ?? [], 48)
    if (!hist) {
      setPlaying(false)
      return
    }
    const STEPS = 48
    const STEP_MS = 250
    let step = 0
    setPlayCursor(hist.minMs)
    const id = setInterval(() => {
      step += 1
      const t = hist.minMs + ((hist.maxMs - hist.minMs) * step) / STEPS
      setPlayCursor(t)
      if (step >= STEPS) {
        clearInterval(id)
        // Hold the full corpus for a beat, then stop.
        setTimeout(() => setPlaying(false), 600)
      }
    }, STEP_MS)
    return () => clearInterval(id)
  }, [playing, mapData])

  // Quality tier — Low (flat 2D) → Ultra (AAA bloom). Persisted per machine.
  const [quality, setQuality] = useState<QualityTier>(loadQuality)
  // Effective (runtime) quality: starts at the persisted tier and is
  // auto-adjusted per-session by PerformanceMonitor. Never touches
  // localStorage — a reload restores the user's chosen tier.
  const [effectiveQuality, setEffectiveQuality] = useState<QualityTier>(loadQuality)
  const settings = QUALITY_SETTINGS[effectiveQuality]
  const pickQuality = useCallback((tier: QualityTier) => {
    saveQuality(tier)
    setQuality(tier)
    // When the user manually picks a tier, also snap the effective quality to it
    // so the selection is immediately visible (PerformanceMonitor may later
    // auto-degrade from the new ceiling if the GPU can't sustain it).
    setEffectiveQuality(tier)
  }, [])

  // Low tier reads the same force layout as a flat 2D knowledge graph.
  const sceneEntities = useMemo(() => {
    if (!data) return []
    return settings.flat ? data.entities.map((e) => ({ ...e, z: 0 })) : data.entities
  }, [data, settings.flat])

  const hoveredEntity = hover ? sceneEntities[hover.index] : undefined

  // Community super-nodes for the collapsed overview — 3D centroids of each
  // community's members at the active Leiden level. Empty (and unrendered)
  // while expanded (collapsedLevel === null) or before the hierarchy loads.
  const supers = useMemo(() => {
    if (collapsedLevel === null || !ancestorIx) return []
    return buildSuperNodes3D(sceneEntities, ancestorIx.ancestorAt, collapsedLevel)
  }, [sceneEntities, ancestorIx, collapsedLevel])

  // Members of the focused community + their bounding sphere (B4.4) — drives
  // both the camera tween (FocusCameraAnimator, below) and the corpus fade
  // (visibility, below). Mirrors buildSuperNodes3D's own id scheme (level <= 0
  // uses the raw community; deeper levels walk to that ancestor) so
  // membership is consistent with whatever level the clicked super-node was
  // built at.
  const focusMembers = useMemo(() => {
    if (!focus || !ancestorIx) return null
    const { communityId, level } = focus
    return sceneEntities.filter((e) => {
      if (!e.community) return false
      const cid = level <= 0 ? e.community : ancestorIx.ancestorAt(e.community, level)
      return cid === communityId
    })
  }, [focus, ancestorIx, sceneEntities])

  const focusSphere = useMemo(() => {
    if (!focusMembers || focusMembers.length === 0) return null
    return boundingSphere(focusMembers.map((m): Vec3 => [m.x, m.y, m.z]))
  }, [focusMembers])

  // Distance the camera was framed at for the current focus sphere — the
  // exit threshold below (FOCUS_EXIT_MULTIPLIER * this) is relative to it,
  // not to the world origin. Purely radius-derived (see framingDistanceFor),
  // so it needs no camera position and can be computed outside <Canvas>.
  const focusFramingDistance = useMemo(
    () => (focusSphere ? framingDistanceFor(focusSphere.radius) : 0),
    [focusSphere],
  )

  // Reliable drill-down exit (B4.4 fix): fires when the camera dollies back
  // out past the focused community's own framed view — see FocusExitSampler
  // in tour-controller.tsx for why this must be focus-relative, not
  // origin-relative.
  const handleFocusExit = useCallback(() => setFocus(null), [])

  // Bypasses collapsedLevel while a community is focused: the camera tween
  // takes real frames to arrive at the community, and CollapseLOD's own
  // hysteresis (driven by actual camera distance, not this component's
  // intent) would otherwise keep rendering the super-node view until the
  // camera physically gets there.
  const showMembers = collapsedLevel === null || focus !== null

  // showMembers -> false unmounts <NeuralLinks>/<InstancedNodes> (collapsed
  // to super-nodes). InstancedNodes' own dragOverrides ref is discarded on
  // that unmount, but pinnedPositionsRef here is owned by this component and
  // survives — left uncleared, it re-bakes edges from a stale drag position
  // on the next expand/drill-down with no matching sphere override, so the
  // edge dangles to empty space. Keyed on showMembers alone (not on data
  // changes) so a routine corpus refetch that leaves members mounted never
  // clears an in-progress pin.
  useEffect(() => {
    if (showMembers) return
    if (pinnedPositionsRef.current.size === 0) return
    pinnedPositionsRef.current.clear()
    setPinVersion((v) => v + 1)
  }, [showMembers])

  // Type filter fades (not hides) non-matching nodes — fade keeps the
  // structural context visible (yWorks KG-demo pattern).
  // Confidence alpha (nodeBaseAlpha) is applied as a base multiplier so
  // low-mention nodes render softer even before type-filter dimming. The
  // type-filter 0.06 replaces the base alpha entirely (it is the dominant
  // visual signal when active), while the base alpha is used on all nodes
  // that pass the filter, giving meaning to node transparency. Community
  // drill-down (B4.4) outranks both when active: the focused community's
  // members render at full alpha, everything else fades to FOCUS_FADE_ALPHA
  // — reuses this exact `visibility` channel, no new per-entity alpha path.
  const visibility = useMemo(() => {
    const ents = data?.entities ?? []
    const arr = new Float32Array(ents.length)
    const focusMemberIds = focusMembers ? new Set(focusMembers.map((m) => m.id)) : null
    for (let i = 0; i < ents.length; i++) {
      if (focusMemberIds) {
        arr[i] = focusMemberIds.has(ents[i].id) ? 1 : FOCUS_FADE_ALPHA
        continue
      }
      const filtered = typeFilter.size > 0 && !typeFilter.has(ents[i].type)
      arr[i] = filtered ? 0.06 : nodeBaseAlpha(ents[i].mention_count ?? 1)
    }
    return arr
  }, [data?.entities, typeFilter, focusMembers])

  // ---------------------------------------------------------------------------
  // Map mode renders CartographerMap — return early with the full map layout.
  // ---------------------------------------------------------------------------
  if (viewMode === "map") {
    // Drain newly ingested entity ids on each render so pulse rings fire once.
    const newIds = drainNewIds()
    return (
      <div
        className="relative h-full w-full bg-background"
        role="application"
        aria-roledescription="knowledge map"
        aria-label="Cartographer knowledge map"
      >
        {/* Exploration toolbar — lens + type filter */}
        <div className="absolute left-3 top-3 z-10 flex flex-col gap-1.5">
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="Color lens"
          >
            {COLOR_LENSES.map((l) => (
              <button
                key={l.id}
                type="button"
                role="radio"
                aria-checked={lens === l.id}
                onClick={() => setLens(l.id)}
                title={l.hint}
                className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                  lens === l.id
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/40"
                }`}
              >
                {l.label}
              </button>
            ))}
          </div>
          {typeChips.length > 1 && (
            <div className="flex max-w-md flex-wrap items-center gap-1" role="group" aria-label="Filter by entity type">
              {typeChips.map(([t, n]) => (
                <button
                  key={t}
                  type="button"
                  aria-pressed={typeFilter.has(t)}
                  onClick={() => toggleType(t)}
                  className={`rounded-full border px-2 py-0.5 text-label-xs transition-colors ${
                    typeFilter.has(t)
                      ? "border-accent bg-accent/30 text-accent-foreground"
                      : "border-border/60 bg-card/70 text-muted-foreground hover:bg-accent/20"
                  }`}
                >
                  {t} <span className="opacity-60">{n}</span>
                </button>
              ))}
              {typeFilter.size > 0 && (
                <button
                  type="button"
                  onClick={() => setTypeFilter(new Set())}
                  className="rounded-full px-2 py-0.5 text-label-xs text-muted-foreground underline-offset-2 hover:underline"
                >
                  clear
                </button>
              )}
            </div>
          )}
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search nodes…"
            aria-label="Search knowledge map"
            className="rounded-md border border-border bg-card/60 px-2 py-1 text-label-xs text-foreground placeholder:text-muted-foreground"
          />
        </div>

        {/* Map scene */}
        <CartographerMap
          focalEntity={focalEntity}
          filter={filter}
          lens={lens}
          typeFilter={typeFilter}
          config={mapConfig}
          data={mapData}
          isLoading={mapLoading}
          isError={mapError}
          errorMessage={mapErrorObj instanceof Error ? mapErrorObj.message : undefined}
          newEntityIds={newIds.size > 0 ? newIds : undefined}
          onInspect={onNodeClick}
          onCommunityClick={handleCommunityClick}
          manualCollapsed={manualCollapsed}
          onManualExpand={toggleManualCollapsed}
          timeFilter={timeFilter}
          layout={layout}
          layoutFallback={mapData?.layout_fallback}
          search={search}
          highlightCommunities={gapHighlight}
        />

        {/* Structural-gaps advisory panel (C2) — top-left, over the map */}
        {showGaps && (
          <StructuralGapsPanel
            gaps={gapsQuery.data?.gaps ?? []}
            isLoading={gapsQuery.isLoading}
            isError={gapsQuery.isError}
            errorMessage={gapsQuery.error instanceof Error ? gapsQuery.error.message : undefined}
            onClose={() => {
              setShowGaps(false)
              setGapHighlight(undefined)
            }}
            onHoverGap={handleGapHover}
            onExplore={handleGapExplore}
          />
        )}

        {/* Timebar (A9 filter + A8 timelapse) — bottom-center over the map */}
        <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2">
          <Timebar
            entities={mapData?.entities ?? []}
            window={timeWindow}
            onWindowChange={setTimeWindow}
            cursor={playing ? playCursor : null}
            playing={playing}
            onTogglePlay={togglePlay}
            canPlay={animate}
          />
        </div>

        {/* Community card (shown when a hull is clicked) */}
        {pinnedCommunity && (
          <div className="absolute bottom-3 left-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {pinnedCommunity.label}
                </div>
                <div className="mt-0.5 text-label-xs text-muted-foreground">
                  {pinnedCommunity.count} entities
                </div>
              </div>
              <button
                type="button"
                onClick={() => setPinnedCommunity(null)}
                aria-label="Close community card"
                className="rounded p-1 text-muted-foreground hover:bg-accent/40"
              >
                ✕
              </button>
            </div>
            {pinnedCommunity.top_hubs.length > 0 && (
              <div className="mt-2">
                <div className="text-label-xs font-medium text-muted-foreground">Top hubs</div>
                <div className="mt-1 flex flex-col gap-0.5">
                  {pinnedCommunity.top_hubs.slice(0, 5).map((hub) => (
                    <button
                      key={hub.id}
                      type="button"
                      onClick={() => onNodeClick?.(hub.id)}
                      className="rounded px-1.5 py-0.5 text-left text-label-xs text-foreground hover:bg-accent/30"
                    >
                      {hub.name}
                      <span className="ml-1 opacity-50">{hub.degree}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="mt-2 flex gap-1.5">
              <button
                type="button"
                onClick={() => toggleManualCollapsed(pinnedCommunity.id)}
                className="flex flex-1 items-center justify-center gap-1 rounded-md border border-border/60 px-2 py-1.5 text-label-xs font-medium text-foreground hover:bg-accent/40"
              >
                {manualCollapsed.has(pinnedCommunity.id) ? (
                  <>
                    <Maximize2 className="size-3" aria-hidden="true" />
                    Expand
                  </>
                ) : (
                  <>
                    <Minimize2 className="size-3" aria-hidden="true" />
                    Collapse
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (pinnedCommunity.top_hubs[0]) onNodeClick?.(pinnedCommunity.top_hubs[0].id)
                }}
                className="flex-1 rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
              >
                Open in Atlas
              </button>
            </div>
          </div>
        )}

        {/* Bottom-right controls: layout presets + map config + view mode toggle */}
        <div className="absolute bottom-3 right-3 z-10 flex items-center gap-1.5">
          {/* Layout presets — client-side, no Redis rows (free-tier cap respected) */}
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="Layout preset"
          >
            {(
              [
                { id: "force", label: "Default map", hint: "Force-directed layout (default)" },
                { id: "wells", label: "Tight clusters", hint: "Well-separated cluster layout" },
                { id: "domain", label: "Domains apart", hint: "Domain-separated layout" },
                { id: "semantic", label: "Semantics", hint: "Embedding-space layout — position reflects meaning" },
              ] as { id: MapLayout; label: string; hint: string }[]
            ).map((preset) => (
              <button
                key={preset.id}
                type="button"
                role="radio"
                aria-checked={layout === preset.id}
                title={preset.hint}
                onClick={() => handleLayout(preset.id)}
                className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                  layout === preset.id
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/40"
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>

          {/* Structural gaps toggle (C2) */}
          <button
            type="button"
            onClick={() => {
              setShowGaps((v) => !v)
              if (showGaps) setGapHighlight(undefined)
            }}
            aria-pressed={showGaps}
            title="Structural gaps — topics that should connect but don't yet"
            className={`flex items-center gap-1 rounded-lg border px-2 py-1 text-label-xs backdrop-blur transition-colors ${
              showGaps
                ? "border-accent bg-accent/30 text-accent-foreground"
                : "border-border/60 bg-card/80 text-muted-foreground hover:bg-accent/40"
            }`}
          >
            <GitMerge className="h-3 w-3" aria-hidden="true" />
            Gaps
          </button>

          {/* Map config popover */}
          <MapConfigPanel
            config={mapConfig}
            onChange={handleMapConfig}
            includeIsolated={includeIsolated}
            onIncludeIsolatedChange={setIncludeIsolated}
            isolatedCount={mapData?.isolated_count ?? 0}
          />

          {/* View mode toggle */}
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="View mode"
          >
            {(["map", "3d", "live"] as ViewMode[]).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={viewMode === m}
                onClick={() => handleViewMode(m)}
                className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                  viewMode === m
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/40"
                }`}
              >
                {VIEW_MODE_LABEL[m]}
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Live mode (B8) — cosmos.gl self-organizing GPU force layout. Shares the 3D
  // embeddings query; its own canvas/WebGL context (never shared with sigma/R3F).
  // ---------------------------------------------------------------------------
  if (viewMode === "live") {
    return (
      <div
        className="relative h-full w-full"
        style={{ background: tokens3D.background }} // drift-allowed: token-routed runtime value
        role="application"
        aria-roledescription="Live self-organizing knowledge graph"
        aria-label={`Live constellation of ${data?.count ?? 0} entities`}
      >
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading live graph…
          </div>
        ) : isError ? (
          <div className="flex h-full items-center justify-center p-6">
            <div className="max-w-md rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error instanceof Error ? error.message : "Failed to load the live graph."}
            </div>
          </div>
        ) : !data || data.entities.length === 0 ? (
          <div className="flex h-full items-center justify-center p-12">
            <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
              <h2 className="text-lg font-semibold text-foreground">No graph to simulate yet</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                Live mode self-organizes your entities in real time. Ingest a
                document and the projection recomputes within a few minutes.
              </p>
            </div>
          </div>
        ) : (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Warming up the simulation…
              </div>
            }
          >
            <CosmosLive
              entities={data.entities}
              links={data.links ?? []}
              colors={nodeColors}
              playing={liveRunning}
              repulsion={liveRepulsion}
              bigBangNonce={liveBigBang}
              reducedMotion={!animate}
              background={tokens3D.background}
              onNodeClick={(id) => onNodeClick?.(id)}
            />
            {/* Live controls — play/pause, re-run big bang, repulsion. */}
            <div className="pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center">
              <div className="liquid-glass pointer-events-auto flex items-center gap-3 rounded-full px-3 py-1.5">
                <button
                  type="button"
                  onClick={() => setLiveRunning((v) => !v)}
                  aria-pressed={liveRunning}
                  aria-label={liveRunning ? "Pause simulation" : "Run simulation"}
                  className="flex items-center gap-1 rounded-full px-2 py-0.5 text-label-xs text-foreground hover:bg-accent/40"
                >
                  {liveRunning ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                  {liveRunning ? "Pause" : "Run"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setLiveBigBang((n) => n + 1)
                    setLiveRunning(true)
                  }}
                  aria-label="Re-run big bang"
                  className="flex items-center gap-1 rounded-full px-2 py-0.5 text-label-xs text-muted-foreground hover:bg-accent/40"
                >
                  <Shuffle className="h-3.5 w-3.5" />
                  Big Bang
                </button>
                <div className="flex items-center gap-1.5">
                  <span className="text-label-xs text-muted-foreground">Repulsion</span>
                  <Slider
                    aria-label="Simulation repulsion"
                    value={[liveRepulsion]}
                    onValueChange={([v]) => setLiveRepulsion(v)}
                    min={0.1}
                    max={2}
                    step={0.1}
                    className="w-24"
                  />
                </div>
              </div>
            </div>
          </Suspense>
        )}

        {/* View mode toggle (bottom-right) */}
        <div className="absolute bottom-3 right-3">
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="View mode"
          >
            {(["map", "3d", "live"] as ViewMode[]).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={viewMode === m}
                onClick={() => handleViewMode(m)}
                className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                  viewMode === m ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/40"
                }`}
              >
                {VIEW_MODE_LABEL[m]}
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // 3D mode — existing R3F scene (unchanged). Guard loading/error/empty here.
  // ---------------------------------------------------------------------------
  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading 3D projection…
      </div>
    )
  }
  if (isError) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error instanceof Error ? error.message : "Failed to load 3D embedding."}
        </div>
      </div>
    )
  }
  if (!data || data.entities.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-12">
        <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
          <h2 className="text-lg font-semibold text-foreground">No 3D projection yet</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            The constellation grows as your knowledge base does. Ingest a
            document and the projection recomputes within a few minutes — or
            run "Constellation 3D coordinate compute" now from Settings →
            Diagnostics → Scheduled Jobs.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className="cerid-stagger-fast relative h-full w-full"
      style={{ ["--i" as string]: 0, background: tokens3D.background }} // drift-allowed: token-routed runtime value
      role="application"
      aria-roledescription="3D knowledge graph"
      aria-label={`Constellation view of ${data.count} entities`}
    >
      <Canvas
        // Remount on persisted tier change: dpr/antialias are GL-context options.
        // effectiveQuality drives rendering; key uses quality (persisted) so a
        // manual tier switch still remounts the context, but auto-degradation
        // adjusts rendering without a remount.
        key={quality}
        // Start outside the structure (force layout spans ~±10 units)
        // so the whole cathedral is in frame on load; users orbit in.
        // Low/2D looks straight down the z-axis at the flattened graph.
        camera={{
          position: settings.flat ? [0, 0, 30] : [0, 6, 28],
          fov: 55,
          near: 0.1,
          far: 1000,
        }}
        gl={{ antialias: settings.antialias, alpha: false }}
        // Set the ceiling DPR from the persisted quality tier; AdaptiveDpr
        // will reduce it under load and restore it when the GPU has headroom.
        dpr={settings.dpr}
      >
        {/*
          AdaptiveDpr drops the pixel ratio under sustained GPU load and
          restores it when the frame budget recovers. The tier's dpr range
          acts as the ceiling; the minimum is always 1.
        */}
        <AdaptiveDpr pixelated />

        {/*
          PerformanceMonitor tracks rolling FPS and fires onDecline after
          `flipflops` consecutive below-threshold windows and onIncline after
          consecutive above-threshold windows. It steps the EFFECTIVE quality
          tier (not localStorage) so a reload restores the user's chosen tier.
          Adaptation is purely per-session and introduces no new motion.
        */}
        <PerformanceMonitor
          ms={500}
          iterations={5}
          threshold={0.9}
          flipflops={3}
          onDecline={() =>
            setEffectiveQuality((prev) => degradeTier(prev))
          }
          onIncline={() =>
            setEffectiveQuality((prev) => upgradeTier(prev, quality))
          }
        />

        <color attach="background" args={[tokens3D.background]} />
        {/* Fog starts past the structure at the default viewing distance —
            it should swallow the starfield's depth, not the graph itself.
            Dark: tight fog (34→95) so the nebula/starfield fade into the void.
            Light: pushed out (44→150) so ambient-occluded spheres stay crisp
            against the bright background instead of muddying into the fog. */}
        {!settings.flat && (
          <fog attach="fog" args={isDark ? [tokens3D.background, 34, 95] : [tokens3D.background, 44, 150]} />
        )}

        {/* Ambient + key lights for material visibility */}
        <ambientLight intensity={0.35} />
        <directionalLight position={[5, 5, 5]} intensity={0.6} color={TRUST_HALO_HEX.verified} />
        <directionalLight position={[-5, -5, -5]} intensity={0.3} color={SURFACE_HEX.brandGold} />

        {/* Parallax starfield backdrop (B4) — 2-3 drei Stars shells at
            differing radius/speed for real depth; budget from the tier. */}
        {settings.starCount > 0 && (
          <ParallaxStarfield count={settings.starCount} animate={animate} ultra={effectiveQuality === "ultra"} />
        )}

        {/* Brand nebula (B4) — faint procedural gas clouds behind the graph.
            Dark-theme only; additive low-alpha color is invisible on light. */}
        {!settings.flat && isDark && settings.starCount > 0 && <NebulaBackdrop />}

        <Suspense fallback={null}>
          {settings.particles && showMembers && (
            <AmbientParticles count={Math.min(800, sceneEntities.length * 4)} radius={18} />
          )}
          {showMembers ? (
            <>
              <NeuralLinks
                entities={sceneEntities}
                links={data.links ?? []}
                animate={animate}
                pulses={settings.pulses && effectiveAmbient}
                float={settings.float && effectiveAmbient}
                hoveredIndex={focusIndex}
                colors={nodeColors}
                visibility={visibility}
                pinnedPositions={pinnedPositionsRef.current}
                pinVersion={pinVersion}
              />
              <InstancedNodes
                entities={sceneEntities}
                animate={animate}
                glow={settings.glow && effectiveAmbient}
                pulses={settings.pulses && effectiveAmbient}
                float={settings.float && effectiveAmbient}
                hoveredIndex={focusIndex}
                neighbors={neighbors}
                degrees={degrees}
                colors={nodeColors}
                visibility={visibility}
                onHover={handleHover}
                onSelect={(id) => {
                  // Click pins the neighborhood (inspection card opens);
                  // the card's "Open in Wiki" action navigates.
                  const idx = sceneEntities.findIndex((e) => e.id === id)
                  setPinned(idx >= 0 ? idx : null)
                }}
                onDragStateChange={setDragging}
                onNodeMoved={handleNodeMoved}
              />
              <HubLabels entities={sceneEntities} degrees={degrees} hoveredIndex={focusIndex} pinnedIndex={pinned} dark={isDark} />
            </>
          ) : (
            // Zoomed-out overview: individual members + links are hidden;
            // one instanced sphere per community, sized by member count.
            // Click a super-node to drill in (B4.4). Super-edges are
            // deferred to a follow-up — see task report.
            <SuperNodes3D supers={supers} onSelect={handleSuperNodeSelect} />
          )}
          {/* Camera-distance LOD sampler — drives collapsedLevel with
              hysteresis (COLLAPSE_IN/COLLAPSE_OUT in supernodes-3d.ts).
              Re-renders React only when the collapsed level actually
              changes, mirroring hub-labels.tsx's bucketed sampler. */}
          <CollapseLOD maxLevel={ancestorIx?.maxLevel ?? -1} onLevelChange={setCollapsedLevel} />
          {/* TourCameraAnimator must be inside <Canvas> — it uses useFrame/useThree */}
          <TourCameraAnimator
            state={tour.state}
            onStopAdvance={tour.advance}
            onComplete={tour.complete}
          />
          {/* Community drill-down camera tween (B4.4) — must be inside
              <Canvas> for useFrame/useThree; idles (center === null) when
              nothing is focused. Reduced motion snaps instead of tweening. */}
          <FocusCameraAnimator
            center={focusSphere?.center ?? null}
            radius={focusSphere?.radius ?? 0}
            instant={!animate}
            controlsRef={controlsRef}
          />
          {/* Reliable focus-relative exit (B4.4 fix) — see FocusExitSampler
              in tour-controller.tsx. Idles (center === null) when nothing is
              focused. */}
          <FocusExitSampler
            center={focusSphere?.center ?? null}
            framingDistance={focusFramingDistance}
            onExit={handleFocusExit}
          />
          {/* Node fly-to (B5): eases the camera to a neighbor picked from the
              kNN panel. Idles (center === null) otherwise. Small radius frames
              the single node close-up; reduced motion snaps. */}
          <FocusCameraAnimator
            center={flyToNode}
            radius={2.5}
            instant={!animate}
            controlsRef={controlsRef}
          />
          {settings.postprocessing && <UltraEffects dark={isDark} />}
        </Suspense>

        <OrbitControls
          ref={controlsRef}
          // Suspended for the duration of a node drag so the camera
          // doesn't orbit under the pointer; re-enabled on drop.
          enabled={!dragging}
          enablePan
          enableZoom
          // Low/2D locks orbit to pan + zoom — a flat knowledge graph.
          enableRotate={!settings.flat}
          zoomSpeed={0.6}
          rotateSpeed={0.4}
          minDistance={2}
          maxDistance={60}
          // Cinematic idle — the cathedral turns slowly until the user
          // takes the controls (drei pauses auto-rotate during interaction
          // and resumes after). Disabled under reduced motion, in 2D,
          // while hovering, while a tour drives the camera, and while
          // dragging a node — three's OrbitControls.update() applies
          // autoRotate regardless of `enabled`, so it needs its own guard.
          autoRotate={animate && settings.autoRotate && tour.state.kind === "idle" && !hover && pinned === null && !dragging}
          autoRotateSpeed={0.35}
        />
      </Canvas>

      {/* kNN neighbors panel (B5) — top-right when a node is pinned. */}
      {pinned !== null && pinnedEntity && (
        <SimilarNeighborsPanel
          pinnedName={pinnedEntity.name}
          neighbors={similarNeighbors}
          onPick={handlePickNeighbor}
          onClose={() => setPinned(null)}
        />
      )}

      {/* Exploration toolbar — lens + type filter (top-left) */}
      <div className="absolute left-3 top-3 flex flex-col gap-1.5">
        <div
          className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
          role="radiogroup"
          aria-label="Color lens"
        >
          {COLOR_LENSES.filter((l) => !l.mapOnly).map((l) => (
            <button
              key={l.id}
              type="button"
              role="radio"
              aria-checked={lens === l.id}
              onClick={() => setLens(l.id)}
              title={l.hint}
              className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                lens === l.id ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/40"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
        {typeChips.length > 1 && (
          <div className="flex max-w-md flex-wrap items-center gap-1" role="group" aria-label="Filter by entity type">
            {typeChips.map(([t, n]) => (
              <button
                key={t}
                type="button"
                aria-pressed={typeFilter.has(t)}
                onClick={() => toggleType(t)}
                className={`rounded-full border px-2 py-0.5 text-label-xs transition-colors ${
                  typeFilter.has(t)
                    ? "border-accent bg-accent/30 text-accent-foreground"
                    : "border-border/60 bg-card/70 text-muted-foreground hover:bg-accent/20"
                }`}
              >
                {t} <span className="opacity-60">{n}</span>
              </button>
            ))}
            {typeFilter.size > 0 && (
              <button
                type="button"
                onClick={() => setTypeFilter(new Set())}
                className="rounded-full px-2 py-0.5 text-label-xs text-muted-foreground underline-offset-2 hover:underline"
              >
                clear
              </button>
            )}
          </div>
        )}
      </div>

      {/* Pinned-entity inspection card (click a node to pin its neighborhood) */}
      {pinned !== null && pinnedEntity && (
        <div className="absolute bottom-3 left-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">{pinnedEntity.name}</div>
              <div className="mt-0.5 text-label-xs text-muted-foreground">
                <span className="uppercase">{pinnedEntity.type}</span>
                {" · "}{pinnedEntity.mention_count} mentions
                {" · "}{neighbors.get(pinned)?.size ?? 0} connections
              </div>
            </div>
            <button
              type="button"
              onClick={() => setPinned(null)}
              aria-label="Clear focus"
              className="rounded p-1 text-muted-foreground hover:bg-accent/40"
            >
              ✕
            </button>
          </div>
          <button
            type="button"
            onClick={() => goTo("subjects", { mode: "wiki", entity: pinnedEntity.id })}
            className="mt-2 w-full rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
          >
            Open in Wiki
          </button>
        </div>
      )}

      {/* Cached/projection-method overlay */}
      <div className="pointer-events-none absolute right-3 top-3 rounded-md bg-card/80 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
        {data.count} entities · {(data.links ?? []).length.toLocaleString()} connections
        {data.cached && " · cached"}
      </div>

      {/* Bottom-right controls: quality tier + view mode toggle */}
      <div className="absolute bottom-3 right-3 flex items-center gap-1.5">
        {/* Quality tier control — Low (2D) → Ultra (AAA) */}
        <div
          className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
          role="radiogroup"
          aria-label="Render quality"
        >
          {QUALITY_TIERS.map((tier) => (
            <button
              key={tier}
              type="button"
              role="radio"
              aria-checked={quality === tier}
              onClick={() => pickQuality(tier)}
              className={`rounded-md px-2 py-1 text-label-xs capitalize transition-colors ${
                quality === tier
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/40"
              }`}
              title={tier === "low" ? "Flat 2D knowledge graph" : tier === "ultra" ? "AAA: HDR bloom postprocessing" : undefined}
            >
              {tier === "low" ? "2D" : tier}
            </button>
          ))}
        </div>

        {/* Ambient toggle — opt-in glow/neon; disabled under prefers-reduced-motion */}
        <button
          type="button"
          aria-pressed={effectiveAmbient}
          disabled={!animate}
          title={
            !animate
              ? "Ambient effects disabled (prefers-reduced-motion)"
              : effectiveAmbient
                ? "Ambient on — click to turn off neon/glow"
                : "Ambient off — click to enable neon/glow"
          }
          onClick={() => setAmbientMode((v) => !v)}
          className={`rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs backdrop-blur transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
            effectiveAmbient
              ? "text-accent-foreground bg-accent/30 border-accent/60"
              : "text-muted-foreground hover:bg-accent/40"
          }`}
        >
          Ambient
        </button>

        {/* Isolated toggle — shown only when isolated entities exist */}
        {(data?.isolated_count ?? 0) > 0 && (
          <label className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40">
            <input
              type="checkbox"
              checked={includeIsolated}
              onChange={(e) => setIncludeIsolated(e.target.checked)}
              className="rounded border-border/60"
            />
            Show isolated ({data?.isolated_count})
          </label>
        )}

        {/* View mode toggle */}
        <div
          className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
          role="radiogroup"
          aria-label="View mode"
        >
          {(["map", "3d", "live"] as ViewMode[]).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={viewMode === m}
              onClick={() => handleViewMode(m)}
              className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                viewMode === m
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/40"
              }`}
            >
              {VIEW_MODE_LABEL[m]}
            </button>
          ))}
        </div>
      </div>

      {/* Hover tooltip — entity card for corpus exploration */}
      {hover && hoveredEntity && (
        <div
          className="pointer-events-none fixed z-50 max-w-xs rounded-lg border border-border/60 bg-card/95 px-3 py-2 shadow-xl backdrop-blur"
          // Runtime-derived position must be inline (drift-allowlisted class
          // of style: popover absolute positioning).
          style={{ left: hover.x + 14, top: hover.y + 14 }} // drift-allowed: popover position derived from live hover coordinates
        >
          <div className="truncate text-sm font-semibold text-foreground">{hoveredEntity.name}</div>
          <div className="mt-0.5 flex items-center gap-2 text-label-xs text-muted-foreground">
            <span className="uppercase">{hoveredEntity.type}</span>
            <span>·</span>
            <span>{hoveredEntity.mention_count} mentions</span>
            <span>·</span>
            <span>{neighbors.get(hover.index)?.size ?? 0} connections</span>
          </div>
          {hoveredEntity.trust_state !== "unknown" && (
            <div className="mt-0.5 flex items-center gap-2 text-label-xs text-muted-foreground">
              <span>{hoveredEntity.trust_state}</span>
            </div>
          )}
          <div className="mt-1 text-label-xs text-muted-foreground/80">Click to open in Wiki</div>
        </div>
      )}

      {/* Tour controls + subtitle overlay */}
      <TourControlPanel
        focalEntity={focalEntity}
        state={tour.state}
        onStart={tour.startTour}
        onPause={tour.pause}
        onResume={tour.resume}
        onStop={tour.stop}
      />
    </div>
  )
}
