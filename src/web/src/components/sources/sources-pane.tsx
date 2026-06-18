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
import { useNavigation } from "@/contexts/navigation-context"
import { useHotkey } from "@/hooks/use-hotkey"
import { KnowledgeStatsHero } from "./knowledge-stats-hero"
import { SourcesHotkeyHelp } from "./sources-hotkey-help"
import { AddSourceFab, type SourceFamily } from "./add-source-fab"
import { SourceAddWizard } from "./source-add-wizard"
import { SourcesHudTicker } from "./sources-hud-ticker"
import { VoiceNoteOverlayHotkeyHost } from "./voice-note-overlay"

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

  const navigation = useNavigation()

  // F2/F3 — wizard state. ``wizardFamily`` filters the kind picker;
  // ``wizardKind`` jumps straight to the configure step when the user
  // entered the wizard from the F1 gallery (kind already known).
  const [wizardOpen, setWizardOpen] = useState(false)
  const [wizardFamily, setWizardFamily] = useState<SourceFamily | undefined>(undefined)
  const [wizardKind, setWizardKind] = useState<string | undefined>(undefined)

  const openWizardWithFamily = useCallback((family: SourceFamily) => {
    setWizardFamily(family)
    setWizardKind(undefined)
    setWizardOpen(true)
  }, [])

  // F10 — Sources hotkey suite. ⌘1-⌘4 jump to sub-tabs; the rest of
  // the documented hotkeys (⌘⇧S, ⌘⇧C, ⌘⇧V, etc.) bind in their
  // respective phase commits when their target surfaces ship.
  useHotkey("meta+1", () => handleModeChange("library"))
  useHotkey("meta+2", () => handleModeChange("activity"))
  useHotkey("meta+3", () => handleModeChange("meetings"))
  useHotkey("meta+4", () => handleModeChange("connectors"))

  return (
    <div className="flex h-full flex-col">
      {/* F6 — live HUD ticker. Thin strip above the F9 hero. */}
      <SourcesHudTicker />

      {/* F9 — Knowledge Stats hero. Pinned at the top of every
          Sources sub-tab. Click-throughs route via NavigationContext
          to the relevant filtered destination. */}
      <div className="shrink-0 px-4 py-3">
        <KnowledgeStatsHero
          onArtifactsClick={() => handleModeChange("library")}
          onChunksClick={() => handleModeChange("library")}
          onEntitiesClick={() => navigation.goTo("subjects")}
          onDiversityClick={() => handleModeChange("connectors")}
        />
      </div>

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

      {/* F2 — Add Source FAB radial menu. ⌘⇧S also toggles. */}
      <AddSourceFab onSelectFamily={openWizardWithFamily} />

      {/* F3 — Add Source wizard. Driven by the FAB and the F1 gallery. */}
      <SourceAddWizard
        open={wizardOpen}
        initialFamily={wizardFamily}
        initialKind={wizardKind}
        onClose={() => setWizardOpen(false)}
        onCreated={() => {
          // Surface the new source in the connectors tab so the user
          // can immediately see it land in the live list.
          handleModeChange("connectors")
        }}
      />

      {/* F10 — hotkey help overlay. Press ? anywhere in the Sources
          pane to surface it. */}
      <SourcesHotkeyHelp />

      {/* F11 — voice-note overlay. ⌘⇧V opens it from anywhere in
          the Sources pane. Liquid Glass surface. */}
      <VoiceNoteOverlayHotkeyHost />
    </div>
  )
}
