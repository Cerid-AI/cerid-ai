// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Constellation — dual-mode knowledge-graph view.
//   "map" (default): flat 2D Cartographer map (sigma.js v3, no physics)
//   "3d" (retained): the existing React Three Fiber cinematic scene
//
// View mode persists in localStorage "cerid-constellation-mode", default "map".
// Both modes share the same server x/y layout so the mental map is stable.

import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react"
import { Canvas } from "@react-three/fiber"
import { AdaptiveDpr, OrbitControls, PerformanceMonitor, Stars } from "@react-three/drei"
import { useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { fetchEmbeddings3D } from "@/lib/api/embeddings-3d"
import { InstancedNodes } from "./instanced-nodes"
import { NeuralLinks } from "./neural-links"
import { HubLabels } from "./hub-labels"
import { AmbientParticles } from "./ambient-particles"
import { TourCameraAnimator, TourControlPanel, useTourState } from "./tour-controller"
import { QUALITY_SETTINGS, QUALITY_TIERS, degradeTier, upgradeTier, loadQuality, saveQuality, type QualityTier } from "./quality"
import { communityRgb, trustRgb, typeRgb, domainRgb, nodeBaseAlpha } from "./palette"
import { CartographerMap } from "./map/CartographerMap"
import { useGraphMap } from "./map/use-graph-map"
import { loadMapConfig, saveMapConfig, type MapConfig } from "./map/map-config"
import type { CommunityHull } from "@/lib/api/graph-map"
import { useNavigation } from "@/contexts/navigation-context"
import { resolveMapTokens, type MapTokens } from "./map/community-layer"
import type { MapLayout } from "@/lib/graph/cycle4-contracts"

// ---------------------------------------------------------------------------
// View-mode persistence
// ---------------------------------------------------------------------------

type ViewMode = "map" | "3d"
const VIEW_MODE_KEY = "cerid-constellation-mode"

function loadViewMode(): ViewMode {
  try {
    const stored = localStorage.getItem(VIEW_MODE_KEY)
    if (stored === "map" || stored === "3d") return stored
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

type ColorLens = "cluster" | "trust" | "type" | "domain"
const COLOR_LENSES: { id: ColorLens; label: string; hint: string }[] = [
  { id: "cluster", label: "Clusters", hint: "Color by knowledge community" },
  { id: "trust", label: "Trust", hint: "Verification bands: green verified · amber partial · red unverified" },
  { id: "type", label: "Types", hint: "Color by entity type" },
  { id: "domain", label: "Domains", hint: "Color by primary knowledge domain (hash-stable; icon + label identify collisions)" },
]

// Postprocessing only loads for Ultra — nobody else pays for the bundle.
const UltraEffects = lazy(() => import("./ultra-effects"))

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

            {/* Hulls toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.hullsVisible}
                onChange={(e) => onChange({ hullsVisible: e.target.checked })}
                className="rounded border-border/60"
              />
              Show community regions
            </label>

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
  const { goTo } = useNavigation()

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

  const handleCommunityClick = useCallback((community: CommunityHull) => {
    setPinnedCommunity((prev) => (prev?.id === community.id ? null : community))
  }, [])

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
    // Only fetch for 3D mode
    enabled: viewMode === "3d",
  })

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

  // Type filter fades (not hides) non-matching nodes — fade keeps the
  // structural context visible (yWorks KG-demo pattern).
  // Confidence alpha (nodeBaseAlpha) is applied as a base multiplier so
  // low-mention nodes render softer even before type-filter dimming. The
  // type-filter 0.06 replaces the base alpha entirely (it is the dominant
  // visual signal when active), while the base alpha is used on all nodes
  // that pass the filter, giving meaning to node transparency.
  const visibility = useMemo(() => {
    const ents = data?.entities ?? []
    const arr = new Float32Array(ents.length)
    for (let i = 0; i < ents.length; i++) {
      const filtered = typeFilter.size > 0 && !typeFilter.has(ents[i].type)
      arr[i] = filtered ? 0.06 : nodeBaseAlpha(ents[i].mention_count ?? 1)
    }
    return arr
  }, [data?.entities, typeFilter])

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

  // Reduced motion collapses growth, pulses, breathing, and auto-rotate.
  const animate = useMemo(
    () => !(typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches),
    [],
  )

  // Ambient mode = opt-in glow/neon; forced off under reduced-motion (Cycle 4 §5).
  const effectiveAmbient = ambientMode && animate

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
          layoutFallback={mapData?.layout_fallback}
          search={search}
        />

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
            <button
              type="button"
              onClick={() => {
                if (pinnedCommunity.top_hubs[0]) onNodeClick?.(pinnedCommunity.top_hubs[0].id)
              }}
              className="mt-2 w-full rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
            >
              Open in Atlas
            </button>
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
            {(["map", "3d"] as ViewMode[]).map((m) => (
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
                {m === "map" ? "Map" : "3D"}
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
            it should swallow the starfield's depth, not the graph itself. */}
        {!settings.flat && <fog attach="fog" args={[tokens3D.background, 34, 95]} />}

        {/* Ambient + key lights for material visibility */}
        <ambientLight intensity={0.35} />
        <directionalLight position={[5, 5, 5]} intensity={0.6} color="#5AECCB" />
        <directionalLight position={[-5, -5, -5]} intensity={0.3} color="#D4AF37" />

        {/* Starfield backdrop — drei's Stars is GPU-friendly */}
        {settings.starCount > 0 && (
          <Stars
            radius={50}
            depth={50}
            count={settings.starCount}
            factor={3}
            saturation={0.2}
            fade
            speed={0.5}
          />
        )}

        <Suspense fallback={null}>
          {settings.particles && (
            <AmbientParticles count={Math.min(800, sceneEntities.length * 4)} radius={18} />
          )}
          <NeuralLinks
            entities={sceneEntities}
            links={data.links ?? []}
            animate={animate}
            pulses={settings.pulses && effectiveAmbient}
            hoveredIndex={focusIndex}
            colors={nodeColors}
            visibility={visibility}
          />
          <InstancedNodes
            entities={sceneEntities}
            animate={animate}
            glow={settings.glow && effectiveAmbient}
            pulses={settings.pulses && effectiveAmbient}
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
          />
          <HubLabels entities={sceneEntities} degrees={degrees} hoveredIndex={focusIndex} />
          {/* TourCameraAnimator must be inside <Canvas> — it uses useFrame/useThree */}
          <TourCameraAnimator
            state={tour.state}
            onStopAdvance={tour.advance}
            onComplete={tour.complete}
          />
          {settings.postprocessing && <UltraEffects />}
        </Suspense>

        <OrbitControls
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
          // while hovering, and while a tour drives the camera.
          autoRotate={animate && settings.autoRotate && tour.state.kind === "idle" && !hover && pinned === null}
          autoRotateSpeed={0.35}
        />
      </Canvas>

      {/* Exploration toolbar — lens + type filter (top-left) */}
      <div className="absolute left-3 top-3 flex flex-col gap-1.5">
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
          {(["map", "3d"] as ViewMode[]).map((m) => (
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
              {m === "map" ? "Map" : "3D"}
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
          style={{ left: hover.x + 14, top: hover.y + 14 }}
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
