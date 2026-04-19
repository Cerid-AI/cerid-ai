// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchSettings, updateSettings, fetchKBStats, fetchProviderCredits } from "@/lib/api"
import type { KBStats } from "@/lib/api"
import type { ServerSettings, SettingsUpdate, ProviderCredits } from "@/lib/types"
import { useUIMode } from "@/contexts/ui-mode-context"
import { cn } from "@/lib/utils"
import { USER_PRESETS } from "@/lib/user-presets"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Button } from "@/components/ui/button"
import { Settings, Loader2, AlertCircle, RefreshCw, Crown } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { PluginsSection } from "./plugins-section"
import { ProSection } from "./pro-section"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { EssentialsSection } from "./essentials-section"
import { PipelineSection } from "./pipeline-section"
import { SystemSection } from "./system-section"
import type { SectionKey } from "./settings-primitives"
import { logSwallowedError } from "@/lib/log-swallowed"

type LoadState = "loading" | "error" | "ready"

const SETTINGS_SECTIONS_VERSION = 6

function readSectionState(): Record<SectionKey, boolean> {
  const defaults: Record<SectionKey, boolean> = {
    connection: true, knowledge_ingestion: true, features: true,
    retrieval: true, search: true, taxonomy: false, infra_sync: true,
    ollama: true, kb_admin: true, credits: true, data_sources: false,
    rag_config: true, watched_folders: false, provider_status: true,
  }
  try {
    const ver = localStorage.getItem("cerid-settings-sections-v")
    if (ver && parseInt(ver, 10) >= SETTINGS_SECTIONS_VERSION) {
      const raw = localStorage.getItem("cerid-settings-sections")
      if (raw) {
        const parsed = JSON.parse(raw) as Record<string, boolean>
        return { ...defaults, ...Object.fromEntries(
          Object.entries(parsed).filter(([k]) => k in defaults)
        ) }
      }
    }
  } catch (err) { logSwallowedError(err, "localStorage.getItem", { key: "cerid-settings-sections" }) }
  return defaults
}

function persistSectionState(state: Record<SectionKey, boolean>) {
  try {
    localStorage.setItem("cerid-settings-sections", JSON.stringify(state))
    localStorage.setItem("cerid-settings-sections-v", String(SETTINGS_SECTIONS_VERSION))
  } catch (err) { logSwallowedError(err, "localStorage.setItem", { key: "cerid-settings-sections" }) }
}

export default function SettingsPane() {
  const queryClient = useQueryClient()
  const [settings, setSettings] = useState<ServerSettings | null>(null)
  const [loadState, setLoadState] = useState<LoadState>("loading")
  const [error, setError] = useState("")
  const [patchError, setPatchError] = useState("")
  const [sections, setSections] = useState<Record<SectionKey, boolean>>(readSectionState)
  const [kbStats, setKBStats] = useState<KBStats | null>(null)
  const [kbLoading, setKBLoading] = useState(false)
  const [kbAction, setKBAction] = useState<string | null>(null)
  const [kbResult, setKBResult] = useState("")
  const [clearConfirmDomain, setClearConfirmDomain] = useState<string | null>(null)
  const { mode: uiMode, setMode: setUIMode } = useUIMode()
  const { data: credits } = useQuery<ProviderCredits>({
    queryKey: ["provider-credits"],
    queryFn: fetchProviderCredits,
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  const [activeTab, setActiveTab] = useState<string>(() => {
    try { return localStorage.getItem("cerid-settings-tab") ?? "essentials" } catch { return "essentials" }
  })

  const handleTabChange = (tab: string) => {
    setActiveTab(tab)
    try { localStorage.setItem("cerid-settings-tab", tab) } catch (err) { logSwallowedError(err, "localStorage.setItem", { key: "cerid-settings-tab" }) }
  }

  const applyUserPreset = async (preset: typeof USER_PRESETS[number]) => {
    setUIMode(preset.uiMode)
    for (const [key, value] of Object.entries(preset.local)) {
      try { localStorage.setItem(key, value) } catch (err) { logSwallowedError(err, "localStorage.setItem", { key }) }
    }
    await patch(preset.settings)
  }

  const loadKBStats = useCallback(async () => {
    setKBLoading(true)
    try {
      const stats = await fetchKBStats()
      setKBStats(stats)
    } catch {
      /* ignore */
    } finally {
      setKBLoading(false)
    }
  }, [])

  useEffect(() => { loadKBStats() }, [loadKBStats])

  const runKBAction = useCallback(async (action: string, fn: () => Promise<{ message: string }>) => {
    setKBAction(action)
    setKBResult("")
    try {
      const result = await fn()
      setKBResult(result.message)
      loadKBStats()
      queryClient.invalidateQueries({ queryKey: ["artifacts"] })
      queryClient.invalidateQueries({ queryKey: ["kb-search"] })
      queryClient.invalidateQueries({ queryKey: ["taxonomy"] })
    } catch (e) {
      setKBResult(e instanceof Error ? e.message : "Action failed")
    } finally {
      setKBAction(null)
    }
  }, [loadKBStats, queryClient])

  const toggleSection = (key: SectionKey) => {
    setSections((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      persistSectionState(next)
      return next
    })
  }

  const load = useCallback(async () => {
    setLoadState("loading")
    setError("")
    try {
      const data = await fetchSettings()
      setSettings(data)
      setLoadState("ready")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch settings")
      setLoadState("error")
    }
  }, [])

  useEffect(() => { load() }, [load])

  const patch = async (update: SettingsUpdate) => {
    if (!settings) return
    const prev = { ...settings }
    setSettings({ ...settings, ...update } as ServerSettings)
    setPatchError("")
    try {
      await updateSettings(update)
    } catch (e) {
      setSettings(prev)
      setPatchError(e instanceof Error ? e.message : "Failed to save")
      setTimeout(() => setPatchError(""), 3000)
    }
  }

  if (loadState === "loading") {
    return (
      <div className="flex h-full flex-col">
        <Header />
        <div className="flex flex-1 items-center justify-center text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading settings...
        </div>
      </div>
    )
  }

  if (loadState === "error" || !settings) {
    return (
      <div className="flex h-full flex-col">
        <Header />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
          <AlertCircle className="h-8 w-8 text-destructive" />
          <p className="text-sm">{error || "Failed to load settings"}</p>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="mr-2 h-3 w-3" />
            Retry
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header />
      {patchError && (
        <div className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          Save failed: {patchError}
        </div>
      )}
      <ScrollArea className="min-h-0 flex-1">
        <TooltipProvider delayDuration={300}>
          <div className="space-y-4 p-4">
            {/* -- User Experience Presets -- */}
            <div className="grid grid-cols-3 gap-2">
              {USER_PRESETS.map((preset) => {
                const isActive = uiMode === preset.uiMode &&
                  settings.enable_hallucination_check === (preset.settings.enable_hallucination_check ?? false) &&
                  settings.enable_feedback_loop === (preset.settings.enable_feedback_loop ?? false) &&
                  settings.enable_memory_extraction === (preset.settings.enable_memory_extraction ?? false)
                const tier = settings.feature_tier ?? "community"
                const locked = preset.requiresPro && tier === "community"
                return (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => !locked && applyUserPreset(preset)}
                    disabled={locked}
                    className={cn(
                      "rounded-lg border p-3 text-left transition-colors",
                      locked
                        ? "opacity-50 cursor-not-allowed border-muted"
                        : isActive
                          ? "border-brand bg-brand/5"
                          : "border-muted hover:border-muted-foreground/30",
                    )}
                  >
                    <div className="flex items-center gap-1.5">
                      <span>{preset.emoji}</span>
                      <span className="text-sm font-medium">{preset.label}</span>
                      {locked && (
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-gold border-gold">
                          <Crown className="mr-0.5 h-2.5 w-2.5" />Pro
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1 text-[11px] leading-tight text-muted-foreground">
                      {preset.description}
                    </p>
                  </button>
                )
              })}
            </div>

            {/* -- Tabbed Settings -- */}
            <Tabs value={activeTab} onValueChange={handleTabChange}>
              <TabsList className="w-full">
                <TabsTrigger value="essentials" className="flex-1">Essentials</TabsTrigger>
                <TabsTrigger value="pipeline" className="flex-1">Pipeline</TabsTrigger>
                <TabsTrigger value="system" className="flex-1">System</TabsTrigger>
                <TabsTrigger value="plugins" className="flex-1">Plugins</TabsTrigger>
                <TabsTrigger value="pro" className="flex-1">
                  <Crown className="mr-1 h-3 w-3" />Pro
                </TabsTrigger>
              </TabsList>

              <TabsContent value="essentials" className="space-y-1 pt-2">
                <PaneErrorBoundary label="Essentials">
                  <EssentialsSection settings={settings} sections={sections} toggleSection={toggleSection} patch={patch} credits={credits} />
                </PaneErrorBoundary>
              </TabsContent>

              <TabsContent value="pipeline" className="space-y-1 pt-2">
                <PaneErrorBoundary label="Pipeline">
                  <PipelineSection settings={settings} sections={sections} toggleSection={toggleSection} patch={patch} />
                </PaneErrorBoundary>
              </TabsContent>

              <TabsContent value="system" className="space-y-1 pt-2">
                <PaneErrorBoundary label="System">
                  <SystemSection
                    settings={settings}
                    sections={sections}
                    toggleSection={toggleSection}
                    patch={patch}
                    credits={credits}
                    kbStats={kbStats}
                    kbLoading={kbLoading}
                    kbAction={kbAction}
                    kbResult={kbResult}
                    loadKBStats={loadKBStats}
                    runKBAction={runKBAction}
                    clearConfirmDomain={clearConfirmDomain}
                    setClearConfirmDomain={setClearConfirmDomain}
                    onRefresh={load}
                  />
                </PaneErrorBoundary>
              </TabsContent>

              <TabsContent value="plugins" className="space-y-1 pt-2">
                <PaneErrorBoundary label="Plugins">
                  <PluginsSection />
                </PaneErrorBoundary>
              </TabsContent>

              <TabsContent value="pro" className="space-y-1 pt-2">
                <PaneErrorBoundary label="Pro">
                  <ProSection
                    featureTier={settings?.feature_tier ?? "community"}
                    featureFlags={settings?.feature_flags ?? {}}
                    onRefresh={load}
                  />
                </PaneErrorBoundary>
              </TabsContent>
            </Tabs>
          </div>
        </TooltipProvider>
      </ScrollArea>
    </div>
  )
}

function Header() {
  return (
    <div className="border-b px-4 py-3">
      <div className="flex items-center gap-2">
        <Settings className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-lg font-semibold">Settings</h2>
      </div>
      <p className="text-xs text-muted-foreground">Server configuration, features, and retrieval pipeline</p>
    </div>
  )
}
