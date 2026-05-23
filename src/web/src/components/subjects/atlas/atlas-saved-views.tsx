// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas saved-views panel — list + save + delete. Floats top-left of
// the Atlas surface alongside the lens chips. Pin an Atlas
// configuration (focal entity + hops + filter + active lenses +
// camera position) as a named view to jump back to it later.

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bookmark, BookmarkPlus, Loader2, X } from "lucide-react"
import {
  createAtlasView,
  deleteAtlasView,
  listAtlasViews,
  type AtlasCameraState,
  type AtlasView,
} from "@/lib/api/atlas-views"
import type { LensId } from "@/lib/graph/lenses"

const KEY = ["atlas-views"] as const

export interface AtlasSavedViewsProps {
  /** Currently focal entity in Atlas */
  focalEntity: string
  hops: number
  filter?: string | null
  activeLenses: Set<LensId>
  /** Snapshot of sigma's current camera, or null if not available */
  getCameraState: () => AtlasCameraState | null
  /** Called when user picks a saved view — restore it */
  onRestore: (view: AtlasView) => void
}

export function AtlasSavedViews({
  focalEntity,
  hops,
  filter,
  activeLenses,
  getCameraState,
  onRestore,
}: AtlasSavedViewsProps) {
  const qc = useQueryClient()
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState("")

  const { data: views, isLoading, isError } = useQuery<AtlasView[]>({
    queryKey: KEY,
    queryFn: () => listAtlasViews(),
    staleTime: 30_000,
  })

  const saveMutation = useMutation({
    mutationFn: createAtlasView,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY })
      setNaming(false)
      setName("")
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteAtlasView,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })

  const handleSave = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    saveMutation.mutate({
      name: trimmed,
      entity: focalEntity,
      hops,
      filter: filter ?? null,
      mode: "atlas",
      lenses: Array.from(activeLenses),
      camera: getCameraState(),
    })
  }

  return (
    <div
      className="absolute left-3 top-3 z-10 w-[240px] rounded-lg border border-border bg-card/95 p-3 shadow-lg backdrop-blur"
      role="group"
      aria-label="Atlas saved views"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-label-xs font-medium uppercase tracking-wide text-muted-foreground">
          Saved views
        </span>
        {!naming && (
          <button
            type="button"
            onClick={() => setNaming(true)}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-label-xs text-foreground/80 hover:bg-accent/40"
            aria-label="Save current view"
          >
            <BookmarkPlus className="h-3 w-3" aria-hidden="true" />
            Save
          </button>
        )}
      </div>

      {naming && (
        <div className="mb-2 flex items-center gap-1">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave()
              else if (e.key === "Escape") {
                setNaming(false)
                setName("")
              }
            }}
            autoFocus
            placeholder="View name"
            maxLength={80}
            className="grow rounded border bg-background px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            aria-label="View name"
          />
          <button
            type="button"
            onClick={handleSave}
            disabled={!name.trim() || saveMutation.isPending}
            className="rounded bg-primary px-2 py-1 text-label-xs text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {saveMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
          </button>
        </div>
      )}

      {isLoading && (
        <div className="flex items-center gap-1 text-label-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
          Loading…
        </div>
      )}
      {isError && (
        <div className="text-label-xs text-destructive">Failed to load saved views</div>
      )}
      {!isLoading && views && views.length === 0 && !naming && (
        <div className="text-label-xs text-muted-foreground">
          No saved views yet. Pin the current Atlas configuration with{" "}
          <BookmarkPlus className="inline h-3 w-3" aria-hidden="true" /> Save.
        </div>
      )}
      {views && views.length > 0 && (
        <ul className="flex max-h-48 flex-col gap-0.5 overflow-y-auto">
          {views.map((view) => (
            <li key={view.view_id} className="group flex items-center gap-1">
              <button
                type="button"
                onClick={() => onRestore(view)}
                className="flex grow items-center gap-1.5 rounded px-1.5 py-1 text-left text-sm text-foreground/85 hover:bg-accent/40"
              >
                <Bookmark className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="grow truncate" title={view.name}>{view.name}</span>
              </button>
              <button
                type="button"
                onClick={() => deleteMutation.mutate(view.view_id)}
                disabled={deleteMutation.isPending}
                className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100 disabled:opacity-30"
                aria-label={`Delete view ${view.name}`}
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
