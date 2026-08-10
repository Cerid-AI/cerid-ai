// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Subjects → Saved views sidebar (Phase M Day 6).
//
// Generalizes the Atlas-specific pinned-views panel: lists saved views
// for the currently active Subjects mode (atlas / constellation /
// timeline / wiki) and exposes restore + delete.
//
// Save-view is left to each mode's own UI (Atlas already ships a
// pin-as-you-tune flow). This sidebar focuses on browse + restore so
// users can move between pinned analytic contexts without leaving
// the Subjects pane.

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Bookmark, Loader2, Trash2 } from "lucide-react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import {
  deleteAtlasView,
  listAtlasViews,
  type AtlasView,
} from "@/lib/api/atlas-views"
import { mcpUrl, mcpHeaders } from "@/lib/api/common"
import type { MapLayoutV2 as MapLayout } from "@/lib/graph/cycle4-contracts"
import { useNavigation } from "@/contexts/navigation-context"

export type SubjectsMode = "atlas" | "constellation" | "timeline" | "wiki"

// Static layout presets for constellation mode — client-side only,
// not persisted to Redis (free-tier cap of 3 user views is untouched).
const LAYOUT_PRESETS: { id: MapLayout; label: string; hint: string }[] = [
  { id: "force", label: "Default map", hint: "Force-directed layout (default)" },
  { id: "wells", label: "Tight clusters", hint: "Well-separated cluster layout" },
  { id: "domain", label: "Domains apart", hint: "Domain-separated layout" },
  { id: "semantic", label: "Semantics", hint: "Embedding-space layout — position reflects meaning" },
]

interface SubjectsViewsSidebarProps {
  mode: SubjectsMode
  /** Click a view → restore it in the current mode. */
  onRestore: (view: AtlasView) => void
  /**
   * Constellation mode only: active layout preset. When provided,
   * the preset chips render above the saved-views list.
   */
  activeLayout?: MapLayout
  /** Called when the user picks a layout preset chip. */
  onLayoutChange?: (layout: MapLayout) => void
  className?: string
}

interface ViewsHealth {
  redis_available: boolean
  max_views_per_user: number
  free_tier_max_views: number
  supported_modes: string[]
  pro_unlocked: boolean
}

async function fetchViewsHealth(): Promise<ViewsHealth> {
  const res = await fetch(mcpUrl("/atlas/views/health").toString(), { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

const KEY_FOR = (mode: SubjectsMode) => ["subjects-views", mode] as const
const HEALTH_KEY = ["subjects-views-health"] as const

export function SubjectsViewsSidebar({
  mode,
  onRestore,
  activeLayout,
  onLayoutChange,
  className,
}: SubjectsViewsSidebarProps) {
  const qc = useQueryClient()

  const { data: views, isLoading, isError } = useQuery({
    queryKey: KEY_FOR(mode),
    queryFn: () => listAtlasViews({ mode }),
    staleTime: 30_000,
  })

  // Tier + free-tier cap come from the backend so the UI hint never
  // drifts from the actual policy (the backend is the source of truth
  // for cap enforcement; see /atlas/views/health, Phase M Day 6).
  const { data: health } = useQuery({
    queryKey: HEALTH_KEY,
    queryFn: fetchViewsHealth,
    staleTime: 60_000,
  })

  const del = useMutation({
    mutationFn: (id: string) => deleteAtlasView(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_FOR(mode) }),
  })

  const isPro = health?.pro_unlocked ?? false
  const freeTierCap = health?.free_tier_max_views ?? 3
  const list = views ?? []
  const { goTo } = useNavigation()
  const showCapHint = !isPro && list.length >= freeTierCap

  return (
    <Card
      className={cn("flex h-full flex-col text-sm", className)}
      data-testid="subjects-views-sidebar"
    >
      <header className="flex items-center gap-2 border-b border-border p-3">
        <Bookmark className="h-4 w-4" aria-hidden="true" />
        <h3 className="text-xs font-semibold uppercase tracking-wider">
          {mode} views
        </h3>
        <span className="ml-auto text-label-xs text-muted-foreground font-mono tabular-nums">
          {list.length}
          {!isPro && `/${freeTierCap}`}
        </span>
      </header>

      <ScrollArea className="grow">
        {/* Layout presets — constellation mode only, client-side (no Redis rows). */}
        {mode === "constellation" && onLayoutChange && (
          <div className="border-b border-border/50 px-3 py-2">
            <div className="mb-1.5 text-label-xs font-medium uppercase tracking-wider text-muted-foreground">
              Layout
            </div>
            <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Layout preset">
              {LAYOUT_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  role="radio"
                  aria-checked={activeLayout === preset.id}
                  title={preset.hint}
                  onClick={() => onLayoutChange(preset.id)}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-label-xs transition-colors",
                    activeLayout === preset.id
                      ? "border-accent bg-accent/30 text-accent-foreground"
                      : "border-border/60 bg-card/70 text-muted-foreground hover:bg-accent/20",
                  )}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {isLoading && (
          <div className="flex items-center justify-center py-6 text-muted-foreground text-xs">
            <Loader2 className="h-3 w-3 animate-spin mr-1.5" />
            Loading…
          </div>
        )}
        {isError && (
          <p className="px-3 py-2 text-xs text-amber-600" role="alert">
            Failed to load views.
          </p>
        )}
        {!isLoading && !isError && list.length === 0 && (
          <p className="px-3 py-4 text-xs text-muted-foreground" data-testid="subjects-views-empty">
            No saved {mode} views yet. Use the Atlas save-view control to pin the
            current configuration.
          </p>
        )}
        <ul className="divide-y divide-border/50">
          {list.map((view) => (
            <li
              key={view.view_id}
              className="group flex items-center gap-1 px-2 py-1.5 hover:bg-accent/40"
              data-testid={`subjects-view-${view.view_id}`}
            >
              <button
                type="button"
                onClick={() => onRestore(view)}
                className="grow text-left text-xs hover:underline truncate"
                title={`${view.name} (${view.entity})`}
              >
                <div className="font-medium truncate">{view.name}</div>
                <div className="text-label-xs text-muted-foreground truncate">
                  {view.hops}-hop
                  {(view.lenses?.length ?? 0) > 0 && ` · ${view.lenses?.join(", ")}`}
                </div>
              </button>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 opacity-0 group-hover:opacity-100"
                onClick={() => del.mutate(view.view_id)}
                aria-label={`Delete view ${view.name}`}
                data-testid={`subjects-view-delete-${view.view_id}`}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </li>
          ))}
        </ul>
      </ScrollArea>

      {showCapHint && (
        <footer className="border-t border-border bg-amber-500/5 px-3 py-2 text-label-xs text-amber-700 dark:text-amber-400">
          Free tier supports {freeTierCap} pinned views.{" "}
          {/* Was plain prose naming an upgrade with no way to reach it. Points
              in-app rather than to the website: the free trial lives here. */}
          <button
            type="button"
            onClick={() => goTo("settings", { category: "plan" })}
            className="font-medium underline underline-offset-2 hover:no-underline"
          >
            Upgrade to Pro
          </button>{" "}
          for unlimited saved views across all modes.
        </footer>
      )}
    </Card>
  )
}
