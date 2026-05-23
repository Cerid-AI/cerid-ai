// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Sources pane — Phase B Day 8. Top-level consolidation surface for
// knowledge ingestion: artifacts, watched folders, external APIs,
// plugins, and the live activity stream.
//
// v1 ships a tabbed layout that embeds the existing KB / settings
// components. Real consolidation (extracting watched-folder + external-
// API UIs out of Settings into Sources) ships in subsequent days; this
// is the routing + shell step.

import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { Loader2, Files, Activity, Settings as SettingsIcon, FileAudio } from "lucide-react"

const KnowledgePane = lazy(() => import("@/components/kb/knowledge-pane"))
const SourcesConnectors = lazy(() =>
  import("./sources-connectors").then((m) => ({ default: m.SourcesConnectors })),
)
const SourcesActivityStream = lazy(() =>
  import("./activity-stream").then((m) => ({ default: m.SourcesActivityStream })),
)
const MeetingsCapturePanel = lazy(() =>
  import("./meetings-capture-panel").then((m) => ({ default: m.MeetingsCapturePanel })),
)

export type SourcesMode = "library" | "activity" | "connectors" | "meetings"

const MODE_DEFS: Array<{
  id: SourcesMode
  label: string
  icon: typeof Files
  description: string
}> = [
  { id: "library", label: "Library", icon: Files, description: "Artifacts + uploads + search" },
  { id: "activity", label: "Activity", icon: Activity, description: "Live ingestion stream" },
  { id: "meetings", label: "Meetings", icon: FileAudio, description: "Upload audio recordings for transcription + diarization" },
  { id: "connectors", label: "Connectors", icon: SettingsIcon, description: "Watched folders + external APIs + plugins" },
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

export default function SourcesPane() {
  const [mode, setMode] = useState<SourcesMode>(() => {
    const m = readQueryParam("sources_mode") as SourcesMode | null
    return m && MODE_DEFS.some((d) => d.id === m) ? m : "library"
  })

  useEffect(() => {
    writeQueryParam("sources_mode", mode === "library" ? null : mode)
  }, [mode])

  const handleModeChange = useCallback((next: SourcesMode) => {
    setMode(next)
  }, [])

  return (
    <div className="flex h-full flex-col">
      {/* Mode switcher header */}
      <div className="flex shrink-0 items-center gap-2 border-b bg-card/40 px-4 py-2">
        <div
          role="tablist"
          aria-label="Sources view mode"
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
                aria-controls={`sources-panel-${def.id}`}
                onClick={() => handleModeChange(def.id)}
                className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors ${
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-foreground/80 hover:bg-accent/40"
                }`}
                title={def.description}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{def.label}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Active mode panel */}
      <div
        id={`sources-panel-${mode}`}
        role="tabpanel"
        className="grow overflow-hidden"
      >
        {mode === "library" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading library…
              </div>
            }
          >
            <KnowledgePane />
          </Suspense>
        )}
        {mode === "activity" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading activity stream…
              </div>
            }
          >
            <SourcesActivityStream />
          </Suspense>
        )}
        {mode === "meetings" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading meeting capture…
              </div>
            }
          >
            <MeetingsCapturePanel />
          </Suspense>
        )}
        {mode === "connectors" && (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading connectors…
              </div>
            }
          >
            <SourcesConnectors />
          </Suspense>
        )}
      </div>
    </div>
  )
}
