// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Subjects pane — consolidates the legacy Knowledge / Wiki / Communities /
// Memories panes into a single surface with a 4-way mode switcher:
//   Atlas (DOM icicle decomposition by default; Neighborhood mode when entity target)
//   Constellation (cartographic map + cosmos.gl Live)
//   Timeline (chronological scrubber, Phase M)
//   Wiki (text page with provenance, Phase A)
//
// Cycle 4 — unified click contract:
//   onInspect(entityId)     — pin entity card, never mode-switch (graph surfaces)
//   onFocusEntity(entityId) — explicit refocus/neighborhood fetch, preserves mode
//   handleEntityPick        — search palette only: sets focal + stays in current surface
//
// Focal entity preserved across mode switches via ?entity= URL param.
// ?mode= remembers the last-used mode for shareable links.
// ?entity= with mode=atlas lands DIRECTLY in Neighborhood mode (E-17 contract).

import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { Bookmark, Compass, Sparkles, Clock, BookOpen, Network, Loader2 } from "lucide-react"
import { useNavigation } from "@/contexts/navigation-context"
import { Atlas } from "./atlas/Atlas"
import { DecompositionIcicle } from "./atlas/decomposition"
import { SubjectsSearchPalette } from "./search-palette"
import { SubjectsViewsSidebar } from "./subjects-views-sidebar"
import { withViewTransition } from "@/lib/view-transitions"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useQuery } from "@tanstack/react-query"
import { listAtlasViews, type AtlasView } from "@/lib/api/atlas-views"
import type { AtlasTierPosition } from "@/lib/graph/cycle4-contracts"

const WikiPane = lazy(() => import("@/components/wiki/wiki-pane"))
// Lazy-load Constellation so the sigma map (and, on demand, the cosmos
// Live chunk) only enters memory when the user opens the mode.
const Constellation = lazy(() => import("./constellation/Constellation"))
const Timeline = lazy(() => import("./timeline/Timeline"))
// RA-11: Leiden community explorer (Phase R.2) — a distinct surface from
// Constellation, which reads a different community-hierarchy endpoint. Was
// shipped but never mounted anywhere reachable; restored here as a mode.
const Communities = lazy(() =>
  import("@/components/kb/graph-explorer").then((m) => ({ default: m.GraphExplorer })),
)
const EntityAnalysisDrawer = lazy(() =>
  import("./constellation/entity-analysis-drawer").then((m) => ({
    default: m.EntityAnalysisDrawer,
  })),
)

export type SubjectsMode = "atlas" | "constellation" | "timeline" | "wiki" | "communities"

const MODE_DEFS: Array<{
  id: SubjectsMode
  label: string
  icon: typeof Compass
  description: string
  available: boolean
}> = [
  { id: "atlas", label: "Atlas", icon: Compass, description: "2D analytic graph", available: true },
  { id: "constellation", label: "Constellation", icon: Sparkles, description: "Cartographic knowledge map", available: true },
  { id: "timeline", label: "Timeline", icon: Clock, description: "Chronological scrubber", available: true },
  { id: "wiki", label: "Wiki", icon: BookOpen, description: "Text page with provenance", available: true },
  { id: "communities", label: "Communities", icon: Network, description: "Leiden community clusters", available: true },
]

// Atlas view sub-state:
//   "overview" = DecompositionIcicle (default)
//   "neighborhood" = Atlas sigma ego view for a focal entity
type AtlasSubMode = "overview" | "neighborhood"

function readQueryParam(name: string): string | null {
  if (typeof window === "undefined") return null
  return new URLSearchParams(window.location.search).get(name)
}

function writeQueryParam(name: string, value: string | null) {
  if (typeof window === "undefined") return
  const params = new URLSearchParams(window.location.search)
  if (value === null || value === "") params.delete(name)
  else params.set(name, value)
  const next = params.toString()
  const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
  window.history.replaceState({}, "", url)
}

function ViewsPopoverButton({
  mode,
  onRestore,
}: {
  mode: SubjectsMode
  onRestore: (view: AtlasView) => void
}) {
  const { data: views, isLoading, isError } = useQuery({
    queryKey: ["subjects-views", mode],
    queryFn: () => listAtlasViews({ mode }),
    staleTime: 30_000,
  })
  const countKnown = !isLoading && !isError
  const count = views?.length ?? 0
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="relative flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-accent/40"
          aria-label={countKnown ? `Saved views (${count})` : "Saved views"}
        >
          <Bookmark className="h-3.5 w-3.5" aria-hidden="true" />
          {countKnown && count > 0 && (
            <span className="min-w-[1rem] rounded-full bg-accent px-1 text-center text-label-xs font-medium leading-4 text-accent-foreground tabular-nums"> {/* drift-allowed: min-w-[1rem] pins badge-centering width for single/double-digit counts */}
              {count}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0" align="end">
        <SubjectsViewsSidebar mode={mode} onRestore={onRestore} className="h-80" />
      </PopoverContent>
    </Popover>
  )
}

export default function SubjectsPane() {
  const [mode, setMode] = useState<SubjectsMode>(() => {
    const m = readQueryParam("mode") as SubjectsMode | null
    return m && MODE_DEFS.some((d) => d.id === m) ? m : "atlas"
  })
  const [focalEntity, setFocalEntity] = useState<string | null>(() => readQueryParam("entity"))
  const [atlasHops, setAtlasHops] = useState<1 | 2 | 3>(() => {
    const h = readQueryParam("hops")
    return h === "1" ? 1 : h === "3" ? 3 : 2
  })
  // Phase K — ?since=ISO scopes Subjects to artifacts ingested after the timestamp.
  const [sinceFilter, setSinceFilter] = useState<string | null>(() => readQueryParam("since"))
  const [paletteOpen, setPaletteOpen] = useState(false)
  // CN2 — Constellation node click opens a right-anchored analysis drawer.
  // This is a sibling OVERLAY (Radix portal), so opening/closing it never
  // remounts or resizes the sigma map container. `null` = drawer closed.
  const [analysisSlug, setAnalysisSlug] = useState<string | null>(null)
  // Keep the drawer mounted after the first open so the Sheet's close
  // animation can run (unmounting on close would cut the exit transition).
  const [drawerMounted, setDrawerMounted] = useState(false)
  const navigation = useNavigation()

  // Atlas sub-mode: overview (decomposition icicle) or neighborhood (sigma ego view).
  // When ?entity= is present on mount OR when an explicit neighborhood action fires,
  // we go directly to neighborhood. Overview is the default when no entity target.
  const [atlasSubMode, setAtlasSubMode] = useState<AtlasSubMode>(() => {
    return readQueryParam("entity") ? "neighborhood" : "overview"
  })

  // atlasTier: saved decomposition ladder position (A2)
  const [atlasTier, setAtlasTier] = useState<AtlasTierPosition | null>(null)

  // Pinned entity card in Neighborhood mode — for onInspect (pin only, no mode switch)
  // This is local state: the pane decides whether to show a card overlay or delegate
  // to the Atlas component's internal card. We delegate to Atlas internals; onInspect
  // simply avoids mode-switching. No extra state needed here for the card itself.

  // Re-read URL-borne state when a goTo carried navigation options. Without
  // this, a same-pane goTo (e.g. Wiki capsule → "Open in Atlas") writes the
  // URL but the pane keeps its mount-time mode/entity and silently no-ops.
  useEffect(() => {
    if (navigation.navVersion === 0) return
    const m = readQueryParam("mode") as SubjectsMode | null
    if (m && MODE_DEFS.some((d) => d.id === m)) setMode(m)
    const e = readQueryParam("entity")
    if (e) {
      setFocalEntity(e)
      // ?entity= always means Neighborhood mode in Atlas (E-17 contract)
      if (!m || m === "atlas") setAtlasSubMode("neighborhood")
    }
    const h = readQueryParam("hops")
    if (h === "1" || h === "2" || h === "3") setAtlasHops(Number(h) as 1 | 2 | 3)
  }, [navigation.navVersion])

  // Reflect mode + entity + hops changes into URL
  useEffect(() => {
    writeQueryParam("mode", mode === "atlas" ? null : mode)
  }, [mode])
  useEffect(() => {
    writeQueryParam("entity", focalEntity)
  }, [focalEntity])
  useEffect(() => {
    writeQueryParam("hops", atlasHops === 2 ? null : String(atlasHops))
  }, [atlasHops])
  useEffect(() => {
    writeQueryParam("since", sinceFilter)
  }, [sinceFilter])

  const handleModeChange = useCallback((next: SubjectsMode) => {
    const def = MODE_DEFS.find((d) => d.id === next)
    if (!def || !def.available) return
    void withViewTransition(() => {
      setMode(next)
    })
  }, [])

  // Search palette pick: sets focal entity. In Atlas mode: switches to Neighborhood
  // for the picked entity. This is the only site that may mode-switch.
  const handleEntityPick = useCallback((entityId: string) => {
    setFocalEntity(entityId)
    setPaletteOpen(false)
    if (mode === "wiki") return
    if (mode === "atlas") setAtlasSubMode("neighborhood")
  }, [mode])

  // Unified click contract — graph surfaces call this to PIN only (no mode switch).
  // Pin is handled internally by the graph surface (Atlas/Cartographer); this
  // callback exists for future cross-surface bookkeeping. No-op at pane level.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const handleInspect = useCallback((_entityId: string) => {
    // intentional no-op at pane level — pin is internal to the graph surface
  }, [])

  // CN2 — Constellation node click. Opens the analysis drawer for the clicked
  // entity. Re-clicking a different node just updates the slug, re-targeting
  // the drawer in place (no remount of the constellation map).
  const handleConstellationNodeClick = useCallback((entityId: string) => {
    setDrawerMounted(true)
    setAnalysisSlug(entityId)
  }, [])

  // Explicit refocus: re-center neighborhood on a different entity (Make focal).
  // Preserves current mode and atlas sub-mode.
  const handleFocusEntity = useCallback((entityId: string) => {
    setFocalEntity(entityId)
    if (mode === "atlas") setAtlasSubMode("neighborhood")
  }, [mode])

  // "Open neighborhood" from icicle entity row → enter Neighborhood mode
  const handleOpenNeighborhood = useCallback((entityId: string) => {
    setFocalEntity(entityId)
    setAtlasSubMode("neighborhood")
  }, [])

  // "Back to overview" from Atlas toolbar → return to decomposition icicle
  const handleBackToOverview = useCallback(() => {
    setAtlasSubMode("overview")
  }, [])

  // Right-click → Cite in chat
  const handleCiteInChat = useCallback((_entityId: string, entityName: string) => {
    navigation.composeChat({ text: `@${entityName} ` })
  }, [navigation])

  // Right-click → Open in Wiki: keep Subjects active, switch mode.
  const handleOpenInWiki = useCallback((entityId: string) => {
    setFocalEntity(entityId)
    setMode("wiki")
  }, [])

  // Open in Timeline from Atlas entity card
  const handleOpenInTimeline = useCallback((entityId: string) => {
    setFocalEntity(entityId)
    setMode("timeline")
  }, [])

  return (
    <div className="flex h-full flex-col">
      {/* ----- Mode switcher header ---------------------------------------- */}
      <div className="flex shrink-0 items-center gap-2 border-b bg-card/40 px-4 py-2">
        <div
          role="tablist"
          aria-label="Subjects view mode"
          className="flex items-center gap-1 rounded-md border border-border bg-background p-0.5"
        >
          {MODE_DEFS.map((def) => {
            const Icon = def.icon
            const isActive = mode === def.id
            return (
              <button
                key={def.id}
                role="tab"
                aria-selected={isActive}
                aria-controls={`subjects-panel-${def.id}`}
                disabled={!def.available}
                onClick={() => handleModeChange(def.id)}
                className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-all duration-200 ${
                  isActive
                    ? "bg-accent text-accent-foreground shadow-[inset_0_0_0_1px_rgba(0,229,216,0.30),0_0_12px_rgba(0,229,216,0.18)]"
                    : def.available
                      ? "text-foreground/80 hover:bg-accent/40"
                      : "cursor-not-allowed text-muted-foreground/50"
                }`}
                title={def.description + (def.available ? "" : " (coming soon)")}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{def.label}</span>
              </button>
            )
          })}
        </div>
        {focalEntity && mode !== "wiki" && mode !== "communities" && (
          <span
            className="ml-2 inline-flex items-center gap-1.5 rounded-full border border-brand/30 bg-brand/5 px-2.5 py-0.5 text-label-xs font-medium text-foreground"
            style={{ viewTransitionName: "focal-entity" }} // drift-allowed: View Transition API requires setting view-transition-name via inline style; no Tailwind utility exists
            aria-label={`Focal entity: ${focalEntity}`}
            title={focalEntity}
            data-testid="subjects-focal-entity-chip"
          >
            {focalEntity.replace(/-/g, " ")}
          </span>
        )}
        <div className="grow" />
        {sinceFilter && (
          <button
            type="button"
            onClick={() => setSinceFilter(null)}
            className="flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-label-xs text-amber-700 hover:bg-amber-500/20"
            aria-label="Clear date filter"
            data-testid="subjects-since-chip"
          >
            <span>Since {sinceFilter.slice(0, 10)}</span>
            <span aria-hidden="true">×</span>
          </button>
        )}
        <ViewsPopoverButton
          mode={mode}
          onRestore={(view) => {
            setFocalEntity(view.entity)
            const validModes: SubjectsMode[] = ["atlas", "constellation", "timeline", "wiki", "communities"]
            if (validModes.includes(view.mode as SubjectsMode)) {
              setMode(view.mode as SubjectsMode)
            }
            // A2: restore atlasTier if present in saved view (v3)
            if (view.mode === "atlas") {
              const v3 = view as AtlasView & { atlasTier?: AtlasTierPosition }
              if (v3.atlasTier) {
                setAtlasTier(v3.atlasTier)
                setAtlasSubMode("overview")
              } else {
                // No tier saved → neighborhood mode if entity is set
                setAtlasSubMode(view.entity ? "neighborhood" : "overview")
              }
            }
          }}
        />
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent/40"
          aria-label="Search subjects"
        >
          <span className="text-label-xs">Search</span>
          <kbd className="rounded border bg-card px-1 text-label-xxs">⌘K</kbd>
        </button>
      </div>

      {/* ----- Active mode panel ------------------------------------------- */}
      <div
        id={`subjects-panel-${mode}`}
        role="tabpanel"
        aria-labelledby={`subjects-tab-${mode}`}
        className="grow overflow-hidden flex"
      >
        <div
          key={mode}
          className={`grow overflow-hidden ${mode === "constellation" ? "mode-swap-deep" : "mode-swap"}`}
        >
        {mode === "atlas" && (
          atlasSubMode === "neighborhood" && focalEntity ? (
            <Atlas
              entity={focalEntity}
              hops={atlasHops}
              onHopsChange={setAtlasHops}
              onSearchPalette={() => setPaletteOpen(true)}
              onInspect={handleInspect}
              onFocusEntity={handleFocusEntity}
              onCiteInChat={handleCiteInChat}
              onOpenInWiki={handleOpenInWiki}
              onOpenInTimeline={handleOpenInTimeline}
              onBackToOverview={handleBackToOverview}
              onRestoreView={(view) => {
                setFocalEntity(view.entity)
                if (view.mode === "atlas" || view.mode === "wiki") {
                  setMode(view.mode as SubjectsMode)
                }
                if (view.version && view.version >= 2 && view.hops) {
                  const h = view.hops
                  if (h === 1 || h === 2 || h === 3) setAtlasHops(h)
                }
              }}
            />
          ) : (
            <DecompositionIcicle
              onInspect={handleInspect}
              onFocusEntity={handleFocusEntity}
              onOpenNeighborhood={handleOpenNeighborhood}
              restoreTier={atlasTier}
              onTierChange={setAtlasTier}
            />
          )
        )}
        {mode === "constellation" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading Constellation…
              </div>
            }
          >
            {/* CN2 — onNodeClick opens the analysis drawer (sibling overlay below),
                NOT a mode switch and NOT a map remount. The drawer is portalled,
                so the sigma map container is untouched when it opens/closes. */}
            <Constellation
              focalEntity={focalEntity ?? undefined}
              onNodeClick={handleConstellationNodeClick}
            />
          </Suspense>
        )}
        {mode === "timeline" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading timeline…
              </div>
            }
          >
            <Timeline
              onEntityPick={handleEntityPick}
              focalEntity={focalEntity}
            />
          </Suspense>
        )}
        {mode === "wiki" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading Wiki…
              </div>
            }
          >
            <WikiPane />
          </Suspense>
        )}
        {mode === "communities" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading Communities…
              </div>
            }
          >
            {/* Member entity pill → stay in Subjects, switch to Wiki (same
                contract as the Atlas/Constellation "Open in Wiki" action)
                instead of GraphExplorer's standalone-mount default, which
                navigates to a top-level "wiki" pane that no longer exists on
                its own. "Ask about this community" keeps GraphExplorer's
                default — it already calls navigation.composeChat(). */}
            <Communities onEntityClick={handleOpenInWiki} />
          </Suspense>
        )}
        </div>
      </div>

      <SubjectsSearchPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onPick={handleEntityPick}
      />

      {/* CN2 — entity analysis drawer. Sibling overlay (portalled), never a
          child of the constellation container, so the sigma map never remounts
          or resizes when it opens/closes. Mounted only after the first node
          click (drawerMounted) so its markdown/chart deps stay out of the
          initial bundle; once mounted it stays mounted and the Sheet's `open`
          prop drives the open/close animation. */}
      {drawerMounted && (
        <Suspense fallback={null}>
          <EntityAnalysisDrawer
            slug={analysisSlug}
            onClose={() => setAnalysisSlug(null)}
            onSelectRelated={(s) => setAnalysisSlug(s)}
          />
        </Suspense>
      )}
    </div>
  )
}
