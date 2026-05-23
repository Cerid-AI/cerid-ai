// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Subjects pane — consolidates the legacy Knowledge / Wiki / Communities /
// Memories panes into a single surface with a 4-way mode switcher:
//   Atlas (2D analytic graph, Phase A)
//   Constellation (3D cinematic, Phase B placeholder)
//   Timeline (chronological scrubber, Phase B placeholder)
//   Wiki (text page with provenance, Phase A wraps existing WikiPane)
//
// Focal entity is preserved across mode switches and surfaced via the
// `?entity=<canonical_id>` URL param (existing convention from
// wiki-pane.tsx). A `?mode=` param remembers the last-used mode for
// shareable links.

import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { Compass, Sparkles, Clock, BookOpen, Loader2 } from "lucide-react"
import { useNavigation } from "@/contexts/navigation-context"
import { Atlas } from "./atlas/Atlas"
import { SubjectsSearchPalette } from "./search-palette"
import { SubjectsViewsSidebar } from "./subjects-views-sidebar"

const WikiPane = lazy(() => import("@/components/wiki/wiki-pane"))
// Lazy-load Constellation so the three.js bundle (~250KB gzipped)
// only enters memory when the user actually picks 3D mode. Keeps the
// initial app load lean — sigma + Atlas alone already adds non-trivial
// weight; we don't want both 2D + 3D engines loaded on first paint.
const Constellation = lazy(() => import("./constellation/Constellation"))
const Timeline = lazy(() => import("./timeline/Timeline"))

export type SubjectsMode = "atlas" | "constellation" | "timeline" | "wiki"

const MODE_DEFS: Array<{
  id: SubjectsMode
  label: string
  icon: typeof Compass
  description: string
  available: boolean
}> = [
  { id: "atlas", label: "Atlas", icon: Compass, description: "2D analytic graph", available: true },
  { id: "constellation", label: "Constellation", icon: Sparkles, description: "3D cinematic view", available: true },
  { id: "timeline", label: "Timeline", icon: Clock, description: "Chronological scrubber", available: true },
  { id: "wiki", label: "Wiki", icon: BookOpen, description: "Text page with provenance", available: true },
]

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

function EmptyFocalPrompt({ onPickEntity }: { onPickEntity: () => void }) {
  return (
    <div className="flex h-full items-center justify-center p-12">
      <div className="max-w-md rounded-xl border border-border bg-card/40 p-8 text-center">
        <h2 className="text-lg font-semibold text-foreground">Pick a starting point</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Atlas renders the neighborhood around a focal entity. Use search
          (<kbd className="rounded border px-1 text-xs">⌘K</kbd>) to pick one,
          or open an entity from the Wiki list.
        </p>
        <button
          type="button"
          onClick={onPickEntity}
          className="mt-6 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Open search palette
        </button>
      </div>
    </div>
  )
}

export default function SubjectsPane() {
  const [mode, setMode] = useState<SubjectsMode>(() => {
    const m = readQueryParam("mode") as SubjectsMode | null
    return m && MODE_DEFS.some((d) => d.id === m) ? m : "atlas"
  })
  const [focalEntity, setFocalEntity] = useState<string | null>(() => readQueryParam("entity"))
  // Phase K — ?since=ISO scopes Subjects to artifacts ingested after the
  // timestamp. Used by the digest notification's "Open" deep-link
  // ("show me what's new since the digest was generated").
  const [sinceFilter, setSinceFilter] = useState<string | null>(() => readQueryParam("since"))
  const [paletteOpen, setPaletteOpen] = useState(false)
  const navigation = useNavigation()

  // Reflect mode + entity changes into URL
  useEffect(() => {
    writeQueryParam("mode", mode === "atlas" ? null : mode)
  }, [mode])
  useEffect(() => {
    writeQueryParam("entity", focalEntity)
  }, [focalEntity])
  useEffect(() => {
    writeQueryParam("since", sinceFilter)
  }, [sinceFilter])

  const handleModeChange = useCallback((next: SubjectsMode) => {
    const def = MODE_DEFS.find((d) => d.id === next)
    if (!def || !def.available) return
    setMode(next)
  }, [])

  const handleSearchPalette = useCallback(() => setPaletteOpen(true), [])
  const handleEntityPick = useCallback((entityId: string) => {
    setFocalEntity(entityId)
    setPaletteOpen(false)
    if (mode === "wiki") return  // wiki handles entity internally
    setMode("atlas")
  }, [mode])

  // Right-click → Cite in chat: build "@Name " seed and switch to chat.
  // No @-mention parser exists yet (chat composer accepts plain text);
  // this format reads natural for the user and primes the eventual
  // parser. See feature note in NavigationProvider's composeChat doc.
  const handleCiteInChat = useCallback((_entityId: string, entityName: string) => {
    navigation.composeChat({ text: `@${entityName} ` })
  }, [navigation])

  // Right-click → Open in Wiki: keep Subjects active, switch mode.
  const handleOpenInWiki = useCallback((entityId: string) => {
    setFocalEntity(entityId)
    setMode("wiki")
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
                className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors ${
                  isActive
                    ? "bg-accent text-accent-foreground"
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
        <button
          type="button"
          onClick={handleSearchPalette}
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
        {/* Mode-aware saved views sidebar (Phase M Day 6).
            Hidden for Atlas, which keeps its existing floating
            saved-views panel adjacent to the lens chips. */}
        {mode !== "atlas" && (
          <div className="hidden lg:flex w-56 shrink-0 border-r border-border bg-card/30 p-2">
            <SubjectsViewsSidebar
              mode={mode}
              onRestore={(view) => {
                setFocalEntity(view.entity)
                const validModes: SubjectsMode[] = ["atlas", "constellation", "timeline", "wiki"]
                if (validModes.includes(view.mode as SubjectsMode)) {
                  setMode(view.mode as SubjectsMode)
                }
              }}
              className="w-full"
            />
          </div>
        )}
        <div className="grow overflow-hidden">
        {mode === "atlas" && (
          focalEntity ? (
            <Atlas
              entity={focalEntity}
              hops={2}
              onSearchPalette={handleSearchPalette}
              onNodeClick={handleEntityPick}
              onNodeDoubleClick={(id) => {
                setFocalEntity(id)
                setMode("wiki")
              }}
              onCiteInChat={handleCiteInChat}
              onOpenInWiki={handleOpenInWiki}
              onRestoreView={(view) => {
                setFocalEntity(view.entity)
                if (view.mode === "atlas" || view.mode === "wiki") {
                  setMode(view.mode)
                }
              }}
            />
          ) : (
            <EmptyFocalPrompt onPickEntity={handleSearchPalette} />
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
            <Constellation
              focalEntity={focalEntity ?? undefined}
              onNodeClick={handleEntityPick}
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
              focalEntity={focalEntity}
              onEntityPick={handleEntityPick}
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
        </div>
      </div>

      <SubjectsSearchPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onPick={handleEntityPick}
      />
    </div>
  )
}
