// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { adminRebuildIndexes, adminRescore, adminRegenerateSummaries, adminClearDomain, fetchOllamaStatus, fetchOllamaRecommendations, enableOllama, disableOllama, pullOllamaModel, fetchHealthStatus, fetchDataSources, enableDataSource, disableDataSource, fetchWatchedFolders, addWatchedFolder, updateWatchedFolder, removeWatchedFolder, scanWatchedFolder, fetchModelUpdates } from "@/lib/api"
import type { ModelUpdatesResponse } from "@/lib/api/settings"
import type { WatchedFolder } from "@/lib/api/settings"
import type { KBStats } from "@/lib/api"
import type { ServerSettings, SettingsUpdate, ProviderCredits, OllamaStatus, OllamaRecommendations, HealthStatusResponse } from "@/lib/types"
import type { SectionKey } from "./settings-primitives"
import { useUIMode } from "@/contexts/ui-mode-context"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Server,
  Tag,
  Cpu,
  Loader2,
  ChevronDown,
  ChevronRight,
  Wrench,
  HardDrive,
  Trash2,
  Lock,
  Globe,
  Download,
  CheckCircle2,
  Copy,
  Check,
  FolderOpen,
  Play,
  AlertTriangle,
  Sparkles,
} from "lucide-react"
import { SyncSection } from "./sync-section"
import { StorageBar } from "./StorageBar"
import { SectionHeading, InfoTip, LabelWithInfo, Row } from "./settings-primitives"

function formatFlagName(flag: string): string {
  return flag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

const TIER_COLORS: Record<string, string> = {
  optimal: "border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400",
  good: "border-blue-500/30 bg-blue-500/10 text-blue-600 dark:text-blue-400",
  degraded: "border-yellow-500/30 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400",
  unknown: "border-muted-foreground/30 bg-muted text-muted-foreground",
}

function InferenceTierRow() {
  const { data: health } = useQuery<HealthStatusResponse>({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
  const inf = health?.inference
  if (!inf) return null

  return (
    <div className="flex items-center justify-between">
      <LabelWithInfo
        label="Inference Tier"
        info={`${inf.message}. Provider: ${inf.provider}, Platform: ${inf.platform}${inf.gpu_name ? `, GPU: ${inf.gpu_name}` : ""}`}
      />
      <div className="flex items-center gap-2">
        <Badge variant="outline" className={cn("text-[10px]", TIER_COLORS[inf.tier] ?? TIER_COLORS.unknown)}>
          {inf.tier === "optimal" ? "Optimal (GPU)" : inf.tier === "good" ? "Good" : inf.tier === "degraded" ? "CPU Only" : "Unknown"}
        </Badge>
        <span className="text-[10px] text-muted-foreground">{inf.provider}</span>
      </div>
    </div>
  )
}

interface SystemSectionProps {
  settings: ServerSettings
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
  patch: (update: SettingsUpdate) => Promise<void>
  credits: ProviderCredits | undefined
  kbStats: KBStats | null
  kbLoading: boolean
  kbAction: string | null
  kbResult: string
  loadKBStats: () => void
  runKBAction: (action: string, fn: () => Promise<{ message: string }>) => void
  clearConfirmDomain: string | null
  setClearConfirmDomain: (d: string | null) => void
  onRefresh: () => void
}

export function SystemSection({
  settings, sections, toggleSection, /* patch — reserved for future settings mutation */
  kbStats, kbLoading, kbAction, kbResult,
  loadKBStats, runKBAction, clearConfirmDomain, setClearConfirmDomain, onRefresh,
}: SystemSectionProps) {
  return (
    <>
      {/* -- Connection -- */}
      <SectionHeading icon={Server} label="Connection" open={sections.connection} onToggle={() => toggleSection("connection")} />
      {sections.connection && (
        <Card className="mb-4">
          <CardContent className="grid gap-3 pt-4">
            <Row label="Server Version" value={settings.version} info="Current MCP server version" />
            <Row label="Machine ID" value={settings.machine_id} mono info="Unique identifier for this server instance" />
            <div className="flex items-center justify-between">
              <LabelWithInfo label="Feature Tier" info="Controls which platform capabilities are available. Set via CERID_TIER env var." />
              <Badge variant={settings.feature_tier === "pro" ? "default" : "secondary"}>
                {settings.feature_tier}
              </Badge>
            </div>
            {/* Inference Tier */}
            <InferenceTierRow />
            {/* Tier-gated capabilities grid */}
            <div className="mt-1 rounded border bg-muted/30 p-3">
              <p className="mb-2 text-[11px] font-medium text-muted-foreground">Platform Capabilities</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                {Object.entries(settings.feature_flags).map(([flag, enabled]) => (
                  <div key={flag} className="flex items-center gap-1.5">
                    <div className={cn("h-1.5 w-1.5 rounded-full", enabled ? "bg-green-500" : "bg-muted-foreground/30")} />
                    <span className="text-[11px] text-muted-foreground">{formatFlagName(flag)}</span>
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* -- Model Updates -- */}
      <ModelUpdatesSection />

      {/* -- Taxonomy -- */}
      <SectionHeading icon={Tag} label="Taxonomy" open={sections.taxonomy} onToggle={() => toggleSection("taxonomy")} />
      {sections.taxonomy && (
        <Card className="mb-4">
          <CardHeader className="px-4 pb-2 pt-4">
            <CardDescription className="text-xs">
              Taxonomy domains organize your knowledge base. Manage domains in the Knowledge tab.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <div className="rounded border">
              {/* Table header */}
              <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                <span className="flex-1">Domain</span>
                <span className="w-20 text-right">Sub-cats</span>
                <span className="w-20 text-right">Artifacts</span>
              </div>
              {/* Table rows */}
              {Object.entries(settings.taxonomy).map(([domain, info]) => {
                const domainStats = kbStats?.domains?.[domain]
                return (
                  <div key={domain} className="flex items-center gap-2 border-b last:border-b-0 px-3 py-1.5 text-xs">
                    <span className="flex items-center gap-1.5 flex-1 min-w-0">
                      <span className="text-sm shrink-0">{info.icon}</span>
                      <span className="capitalize truncate">{domain}</span>
                    </span>
                    <span className="w-20 text-right tabular-nums text-muted-foreground">
                      {info.sub_categories.length}
                    </span>
                    <span className="w-20 text-right tabular-nums text-muted-foreground">
                      {domainStats?.artifacts ?? "\u2014"}
                    </span>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* -- Storage Usage -- */}
      <StorageBar />

      {/* -- Infrastructure & Sync -- */}
      <SectionHeading icon={Wrench} label="Infrastructure & Sync" open={sections.infra_sync} onToggle={() => toggleSection("infra_sync")} />
      {sections.infra_sync && (
        <>
          <Card className="mb-2">
            <CardContent className="grid gap-3 pt-4">
              <Row label="Bifrost URL" value={settings.bifrost_url ?? "\u2014"} mono info="LLM gateway endpoint" />
              <Row label="Bifrost Timeout" value={settings.bifrost_timeout ? `${settings.bifrost_timeout}s` : "\u2014"} info="Request timeout for LLM calls" />
              <Row label="ChromaDB" value={settings.chroma_url ?? "\u2014"} mono info="Vector database endpoint" />
              <Row label="Neo4j" value={settings.neo4j_uri ?? "\u2014"} mono info="Graph database endpoint" />
              <Row label="Redis" value={settings.redis_url ?? "\u2014"} mono info="Cache and BM25 index (password redacted)" />
              <Row label="Archive Path" value={settings.archive_path ?? "\u2014"} mono info="File archive mount path" />
              <Row label="Chunking Mode" value={settings.chunking_mode ?? "\u2014"} info="Token-based or semantic chunking" />
              <div className="my-1 h-px bg-border" />
              <div className="flex items-center justify-between">
                <LabelWithInfo label="Encryption" info="Whether data at rest is encrypted" />
                <Badge variant={settings.enable_encryption ? "default" : "secondary"}>
                  {settings.enable_encryption ? "Enabled" : "Disabled"}
                </Badge>
              </div>
              <Row label="Sync Backend" value={settings.sync_backend} info="Storage backend for cross-device sync" />
            </CardContent>
          </Card>
          <SyncSection />
        </>
      )}

      {/* -- Local LLM (Ollama) -- */}
      <SectionHeading icon={Cpu} label="Local LLM (Ollama)" open={sections.ollama} onToggle={() => toggleSection("ollama")} />
      {sections.ollama && <OllamaSection settings={settings} onRefresh={onRefresh} />}

      {/* -- Data Sources -- */}
      <DataSourcesSection sections={sections} toggleSection={toggleSection} />

      {/* -- Watched Folders -- */}
      <SectionHeading icon={FolderOpen} label="Watched Folders" open={sections.watched_folders ?? false} onToggle={() => toggleSection("watched_folders")} />
      {(sections.watched_folders ?? false) && <WatchedFoldersSection />}

      {/* -- KB Management -- */}
      <SectionHeading icon={HardDrive} label="KB Management" open={sections.kb_admin} onToggle={() => toggleSection("kb_admin")} />
      {sections.kb_admin && (
        <KBManagementSection
          kbStats={kbStats}
          kbLoading={kbLoading}
          kbAction={kbAction}
          kbResult={kbResult}
          loadKBStats={loadKBStats}
          runKBAction={runKBAction}
          clearConfirmDomain={clearConfirmDomain}
          setClearConfirmDomain={setClearConfirmDomain}
        />
      )}
    </>
  )
}

/* ---- KB Management sub-component ---- */

function KBManagementSection({
  kbStats, kbLoading, kbAction, kbResult,
  loadKBStats, runKBAction, clearConfirmDomain, setClearConfirmDomain,
}: {
  kbStats: KBStats | null
  kbLoading: boolean
  kbAction: string | null
  kbResult: string
  loadKBStats: () => void
  runKBAction: (action: string, fn: () => Promise<{ message: string }>) => void
  clearConfirmDomain: string | null
  setClearConfirmDomain: (d: string | null) => void
}) {
  return (
    <Card className="mb-4">
      <CardContent className="grid gap-3 pt-4">
        {kbStats && (
          <div className="rounded border bg-muted/30 p-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Total artifacts</span>
              <span className="font-mono">{kbStats.total_artifacts}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Total chunks</span>
              <span className="font-mono">{kbStats.total_chunks}</span>
            </div>
            {Object.entries(kbStats.domains).map(([domain, info]) => (
              <div key={domain} className="mt-1 flex items-center justify-between gap-2 border-t pt-1">
                <span className="truncate text-muted-foreground">{domain}</span>
                <div className="flex items-center gap-2">
                  <span className="font-mono">{info.artifacts} / {info.chunks}</span>
                  {info.artifacts > 0 && clearConfirmDomain !== domain && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-5 w-5 p-0 text-destructive/60 hover:text-destructive"
                      onClick={() => setClearConfirmDomain(domain)}
                      title={`Clear ${domain}`}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  )}
                  {clearConfirmDomain === domain && (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="destructive"
                        size="sm"
                        className="h-5 px-1 text-[10px]"
                        disabled={kbAction !== null}
                        onClick={() => {
                          setClearConfirmDomain(null)
                          runKBAction("clear", () => adminClearDomain(domain))
                        }}
                      >
                        Clear
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-5 px-1 text-[10px]"
                        onClick={() => setClearConfirmDomain(null)}
                      >
                        Cancel
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        {kbLoading && !kbStats && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading KB stats...
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            className="text-xs"
            disabled={kbAction !== null}
            onClick={() => runKBAction("rebuild", adminRebuildIndexes)}
          >
            {kbAction === "rebuild" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            Rebuild Indexes
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-xs"
            disabled={kbAction !== null}
            onClick={() => runKBAction("rescore", adminRescore)}
          >
            {kbAction === "rescore" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            Rescore All
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-xs"
            disabled={kbAction !== null}
            onClick={() => runKBAction("summaries", adminRegenerateSummaries)}
          >
            {kbAction === "summaries" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            Regenerate Summaries
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-xs"
            disabled={kbLoading}
            onClick={loadKBStats}
          >
            {kbLoading && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            Refresh Stats
          </Button>
        </div>

        {kbResult && (
          <p className="rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground">{kbResult}</p>
        )}
      </CardContent>
    </Card>
  )
}

/* ---- Ollama sub-component ---- */

const STAGE_LABELS: Record<string, string> = {
  claim_extraction: "Claim Extraction",
  query_decomposition: "Query Decomposition",
  topic_extraction: "Topic Extraction",
  memory_resolution: "Memory Resolution",
  verification_simple: "Simple Verification",
  verification_complex: "Complex Verification",
  reranking: "Reranking",
  chat_generation: "Chat Generation",
}

const LOCKED_STAGES = new Set(["verification_complex", "chat_generation"])

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      size="sm" variant="ghost"
      className="h-6 w-6 p-0 shrink-0"
      onClick={() => { navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) }) }}
      aria-label="Copy to clipboard"
    >
      {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
    </Button>
  )
}

function OllamaSection({ settings, onRefresh }: { settings: ServerSettings; onRefresh: () => void }) {
  const { data: ollamaStatus, refetch: refetchOllama, isLoading } = useQuery<OllamaStatus>({
    queryKey: ["ollama-status"],
    queryFn: fetchOllamaStatus,
    staleTime: 30_000,
  })
  const { data: healthStatus } = useQuery<HealthStatusResponse>({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 30_000,
    retry: 1,
  })
  const { mode: uiMode } = useUIMode()
  const [toggling, setToggling] = useState(false)
  const [pipelineOpen, setPipelineOpen] = useState(false)
  // Wizard state: null (idle), "install", "polling", "model-select", "pulling", "enabling", "complete"
  const [wizardPhase, setWizardPhase] = useState<string | null>(null)
  const [wizardError, setWizardError] = useState<string | null>(null)
  const [pullProgress, setPullProgress] = useState("")
  const [modelRecs, setModelRecs] = useState<OllamaRecommendations | null>(null)
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  const [modelMgmtOpen, setModelMgmtOpen] = useState(false)
  const [switching, setSwitching] = useState(false)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const handleToggle = useCallback(async () => {
    if (!ollamaStatus) return
    setToggling(true)
    try {
      if (settings.internal_llm_provider === "ollama") {
        await disableOllama()
      } else {
        await enableOllama()
      }
      await refetchOllama()
      onRefresh()
    } catch (e) {
      console.warn("Ollama toggle failed:", e)
    }
    setToggling(false)
  }, [ollamaStatus, settings.internal_llm_provider, refetchOllama, onRefresh])

  // Start the wizard — show install instructions first, then poll for connectivity
  const startWizard = useCallback(() => {
    setWizardError(null)
    setPullProgress("")
    setWizardPhase("install")
  }, [])

  // Pull model and enable Ollama — shared by wizard and direct setup
  const pullAndEnable = useCallback(async (model: string) => {
    setWizardPhase("pulling")
    setPullProgress(`Downloading ${model}...`)
    try {
      const res = await pullOllamaModel(model)
      if (res.body) {
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const text = decoder.decode(value, { stream: true })
          for (const line of text.split("\n").filter(Boolean)) {
            try {
              const evt = JSON.parse(line.replace(/^data:\s*/, ""))
              if (evt.total && evt.completed) {
                const pct = Math.round((evt.completed / evt.total) * 100)
                setPullProgress(`Downloading ${model}... ${pct}%`)
              } else if (evt.status) {
                setPullProgress(evt.status)
              }
            } catch { /* ignore parse errors */ }
          }
        }
      }
    } catch (e) {
      setWizardPhase(null)
      setWizardError(e instanceof Error ? e.message : "Model pull failed")
      return
    }
    // Enable with selected model
    setWizardPhase("enabling")
    try {
      await enableOllama(model)
      await refetchOllama()
      onRefresh()
      setWizardPhase("complete")
      setTimeout(async () => {
        await refetchOllama()
        onRefresh()
        setWizardPhase(null)
      }, 3000)
    } catch (e) {
      setWizardPhase(null)
      setWizardError(e instanceof Error ? e.message : "Enable failed")
    }
  }, [refetchOllama, onRefresh])

  // Poll for Ollama becoming reachable, then show model selection
  const startPolling = useCallback(() => {
    setWizardPhase("polling")
    setWizardError(null)
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = setInterval(async () => {
      try {
        const status = await fetchOllamaStatus()
        if (status.reachable) {
          if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null }
          await refetchOllama()
          // Fetch hardware-aware recommendations and show model selection
          try {
            const recs = await fetchOllamaRecommendations()
            setModelRecs(recs)
            setSelectedModel(recs.recommended)
            setWizardPhase("model-select")
          } catch {
            // Fallback: skip model selection, use default
            const fallbackModel = status.default_model || "llama3.2:3b"
            setSelectedModel(fallbackModel)
            await pullAndEnable(fallbackModel)
          }
        }
      } catch { /* polling error — keep trying */ }
    }, 3000)
  }, [refetchOllama, pullAndEnable])

  // Cleanup polling on unmount
  useEffect(() => () => { if (pollingRef.current) clearInterval(pollingRef.current) }, [])

  // Load recommendations for the model management panel
  const loadRecommendations = useCallback(async () => {
    if (modelRecs) return // already loaded
    try {
      const recs = await fetchOllamaRecommendations()
      setModelRecs(recs)
    } catch { /* non-critical */ }
  }, [modelRecs])

  // Switch active model (pull if needed, then enable)
  const switchModel = useCallback(async (modelId: string) => {
    setSwitching(true)
    setWizardError(null)
    try {
      // Check if model is already installed
      const installed = ollamaStatus?.models ?? []
      if (!installed.includes(modelId)) {
        await pullAndEnable(modelId)
      } else {
        // Already installed — just switch
        await enableOllama(modelId)
        await refetchOllama()
        onRefresh()
      }
      setModelMgmtOpen(false)
    } catch (e) {
      setWizardError(e instanceof Error ? e.message : "Model switch failed")
    }
    setSwitching(false)
  }, [ollamaStatus, pullAndEnable, refetchOllama, onRefresh])

  const isActive = settings.internal_llm_provider === "ollama"
  const hwLabel = settings.ollama_url?.includes("cerid-ollama") ? "Docker container" : settings.ollama_url ?? "\u2014"
  const ollamaReachable = ollamaStatus?.reachable ?? false
  const showPipelineRouting = uiMode === "advanced"
  const pipelineProviders = healthStatus?.pipeline_providers ?? settings.pipeline_providers

  return (
    <Card className="mb-2">
      <CardHeader className="pb-2 pt-4 px-4">
        <CardTitle className="text-sm flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          Ollama — Local LLM Add-On
        </CardTitle>
        <CardDescription className="text-xs">
          Free local inference for pipeline tasks (verification context, query decomposition, memory resolution, claim extraction).
          Model auto-recommended based on your hardware. Falls back to OpenRouter when unavailable.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 pt-0">
        {/* Status */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Status</span>
          {isLoading ? (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
          ) : ollamaStatus?.reachable ? (
            <Badge variant="default" className="text-[10px] bg-green-500/20 text-green-700 dark:text-green-400 border-green-500/30">
              Connected ({ollamaStatus.models.length} model{ollamaStatus.models.length !== 1 ? "s" : ""})
            </Badge>
          ) : ollamaStatus?.enabled ? (
            <Badge variant="outline" className="text-[10px] text-amber-600 dark:text-yellow-400 border-yellow-500/30">
              Enabled but unreachable
            </Badge>
          ) : (
            <Badge variant="secondary" className="text-[10px]">Not installed</Badge>
          )}
        </div>

        {/* Setup wizard — shown when Ollama is not reachable, or when wizard is actively running */}
        {((!ollamaReachable && !isActive) || wizardPhase) && (
          <div className="rounded-lg border border-dashed border-muted-foreground/30 p-3 space-y-3">
            {wizardPhase === "complete" ? (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400">
                  <CheckCircle2 className="h-4 w-4" />
                  <span className="font-medium">Ollama is set up and running!</span>
                </div>
                <p className="text-[11px] text-muted-foreground">Pipeline tasks (claim extraction, query decomposition, memory resolution) will now run locally for $0. This section will update momentarily.</p>
              </div>
            ) : wizardPhase === "enabling" ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span>Enabling Ollama routing...</span>
              </div>
            ) : wizardPhase === "pulling" ? (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>{pullProgress || "Pulling model..."}</span>
                </div>
                <p className="text-[10px] text-muted-foreground/60">This may take a few minutes depending on your connection.</p>
              </div>
            ) : wizardPhase === "model-select" && modelRecs ? (
              <div className="space-y-3">
                <div>
                  <p className="text-xs font-medium">Choose a Model</p>
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    Detected {modelRecs.hardware.ram_gb}GB RAM
                    {modelRecs.hardware.gpu ? ` \u00b7 ${modelRecs.hardware.gpu}` : ""}
                  </p>
                </div>
                <div className="space-y-1.5">
                  {modelRecs.models.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      disabled={!m.compatible}
                      className={cn(
                        "w-full rounded-lg border p-2.5 text-left transition-colors",
                        selectedModel === m.id
                          ? "border-brand bg-brand/5"
                          : m.compatible
                            ? "border-border hover:border-muted-foreground/50"
                            : "border-border opacity-40 cursor-not-allowed",
                      )}
                      onClick={() => m.compatible && setSelectedModel(m.id)}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium">{m.name}</span>
                          <span className="text-[10px] text-muted-foreground">{m.size_gb}GB</span>
                          {m.recommended && (
                            <Badge variant="default" className="text-[9px] h-4 bg-brand/20 text-brand border-brand/30">
                              Recommended
                            </Badge>
                          )}
                        </div>
                        <span className="text-[10px] text-muted-foreground">{m.origin}</span>
                      </div>
                      <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">{m.description}</p>
                      {!m.compatible && (
                        <p className="text-[10px] text-red-500 mt-0.5">Requires {m.min_ram_gb}GB+ RAM</p>
                      )}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="default"
                    className="h-7 text-xs"
                    disabled={!selectedModel}
                    onClick={() => selectedModel && pullAndEnable(selectedModel)}
                  >
                    Install {modelRecs.models.find((m) => m.id === selectedModel)?.name ?? selectedModel}
                  </Button>
                  <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setWizardPhase(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : wizardPhase === "polling" ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>Waiting for Ollama to start...</span>
                </div>
                <p className="text-[10px] text-muted-foreground/60">
                  Checking every 3 seconds. Once detected, model download and setup will continue automatically.
                </p>
                <Button size="sm" variant="ghost" className="h-6 text-[10px]" onClick={() => { if (pollingRef.current) clearInterval(pollingRef.current); setWizardPhase(null) }}>
                  Cancel
                </Button>
              </div>
            ) : wizardPhase === "install" ? (
              <div className="space-y-3">
                <p className="text-xs font-medium">Install Ollama</p>
                <ol className="text-[11px] text-muted-foreground list-decimal list-inside space-y-0.5">
                  <li>Open <strong>Terminal</strong> (macOS: Spotlight → &quot;Terminal&quot;, Linux: Ctrl+Alt+T, Windows: PowerShell)</li>
                  <li>Copy the command below and paste it into your terminal</li>
                  <li>Click <strong>Continue</strong> — Cerid will detect Ollama and finish setup automatically</li>
                </ol>
                <div className="space-y-1.5">
                  {[
                    { os: "macOS", cmd: "curl -fsSL https://ollama.com/install.sh | sh && open -a Ollama" },
                    { os: "Linux", cmd: "curl -fsSL https://ollama.com/install.sh | sh && ollama serve" },
                  ].map(({ os, cmd }) => (
                    <div key={os} className="flex items-center gap-2 rounded border bg-muted/50 px-2.5 py-1.5">
                      <span className="text-[10px] font-semibold text-muted-foreground w-10 shrink-0">{os}</span>
                      <code className="flex-1 text-[10px] truncate select-all">{cmd}</code>
                      <CopyButton text={cmd} />
                    </div>
                  ))}
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="default" className="h-7 text-xs" onClick={startPolling}>
                    Continue — detect Ollama
                  </Button>
                  <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setWizardPhase(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <p className="text-xs text-muted-foreground">
                  Run pipeline tasks locally for free with a small local LLM. No API costs for internal operations.
                </p>
                <Button size="sm" variant="default" className="h-7 text-xs" onClick={startWizard}>
                  <Download className="mr-1 h-3 w-3" />
                  Set Up Ollama
                </Button>
              </>
            )}
            {wizardError && (
              <div className="space-y-1">
                <p className="text-xs text-red-600 dark:text-red-400">{wizardError}</p>
                <Button size="sm" variant="outline" className="h-6 text-[10px]" onClick={startWizard}>Try again</Button>
              </div>
            )}
          </div>
        )}

        {/* Active toggle */}
        <div className="flex items-center justify-between">
          <LabelWithInfo label="Route pipeline tasks to Ollama" info="When on, claim extraction, query decomposition, memory resolution, and topic extraction use the local model. When off, uses OpenRouter." />
          <Switch
            checked={isActive}
            onCheckedChange={handleToggle}
            disabled={toggling || (!ollamaReachable && !isActive)}
          />
        </div>
        {/* Model + connection — show when reachable or active */}
        {(ollamaReachable || isActive) && (
          <>
            {/* Model selector */}
            <div className="flex items-center justify-between">
              <LabelWithInfo label="Active model" info="The local LLM used for pipeline tasks. Click Change to see available options." />
              <div className="flex items-center gap-2">
                <code className="text-[11px] text-muted-foreground font-mono">
                  {settings.internal_llm_model || ollamaStatus?.default_model || "—"}
                </code>
                {ollamaReachable && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[10px]"
                    disabled={switching || !!wizardPhase}
                    onClick={() => { setModelMgmtOpen((v) => !v); loadRecommendations() }}
                  >
                    Change
                  </Button>
                )}
              </div>
            </div>

            {/* Model management panel */}
            {modelMgmtOpen && ollamaReachable && (
              <div className="rounded-lg border border-dashed border-muted-foreground/30 p-3 space-y-3">
                <div>
                  <p className="text-xs font-medium">Model Selection</p>
                  {modelRecs && (
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      {modelRecs.hardware.ram_gb}GB RAM
                      {modelRecs.hardware.gpu && modelRecs.hardware.gpu !== "CPU only" ? ` · ${modelRecs.hardware.gpu}` : ""}
                      {modelRecs.hardware.cpu ? ` · ${modelRecs.hardware.cpu}` : ""}
                    </p>
                  )}
                </div>
                {modelRecs ? (
                  <div className="space-y-1.5">
                    {modelRecs.models.map((m) => {
                      const isInstalled = ollamaStatus?.models.includes(m.id) ?? false
                      const isCurrent = (settings.internal_llm_model || ollamaStatus?.default_model) === m.id
                      return (
                        <div
                          key={m.id}
                          className={cn(
                            "rounded-lg border p-2.5 transition-colors",
                            isCurrent
                              ? "border-brand bg-brand/5"
                              : m.compatible
                                ? "border-border"
                                : "border-border opacity-40",
                          )}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-medium">{m.name}</span>
                              <span className="text-[10px] text-muted-foreground">{m.size_gb}GB · {m.origin}</span>
                              {m.recommended && (
                                <Badge variant="default" className="text-[9px] h-4 bg-brand/20 text-brand border-brand/30">
                                  Recommended
                                </Badge>
                              )}
                              {isCurrent && (
                                <Badge variant="default" className="text-[9px] h-4 bg-green-500/20 text-green-600 dark:text-green-400 border-green-500/30">
                                  Active
                                </Badge>
                              )}
                              {isInstalled && !isCurrent && (
                                <Badge variant="secondary" className="text-[9px] h-4">
                                  Installed
                                </Badge>
                              )}
                            </div>
                            {m.compatible && !isCurrent && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-6 text-[10px]"
                                disabled={switching || !!wizardPhase}
                                onClick={() => switchModel(m.id)}
                              >
                                {isInstalled ? "Use" : "Install & Use"}
                              </Button>
                            )}
                          </div>
                          <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">{m.strengths}</p>
                          {!m.compatible && (
                            <p className="text-[10px] text-red-500 mt-0.5">Requires {m.min_ram_gb}GB+ RAM</p>
                          )}
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>Loading model options...</span>
                  </div>
                )}
                {/* Installed models not in catalog */}
                {ollamaStatus && modelRecs && (() => {
                  const catalogIds = new Set(modelRecs.models.map((m) => m.id))
                  const extra = ollamaStatus.models.filter((m) => !catalogIds.has(m))
                  if (extra.length === 0) return null
                  return (
                    <div className="space-y-1">
                      <p className="text-[10px] text-muted-foreground font-medium">Other installed models</p>
                      {extra.map((m) => {
                        const isCurrent = (settings.internal_llm_model || ollamaStatus.default_model) === m
                        return (
                          <div key={m} className={cn("flex items-center justify-between rounded-md border px-2.5 py-1.5", isCurrent ? "border-brand bg-brand/5" : "border-border")}>
                            <div className="flex items-center gap-2">
                              <code className="text-[11px] font-mono">{m}</code>
                              {isCurrent && (
                                <Badge variant="default" className="text-[9px] h-4 bg-green-500/20 text-green-600 dark:text-green-400 border-green-500/30">
                                  Active
                                </Badge>
                              )}
                            </div>
                            {!isCurrent && (
                              <Button size="sm" variant="outline" className="h-6 text-[10px]" disabled={switching} onClick={() => switchModel(m)}>
                                Use
                              </Button>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )
                })()}
                {wizardError && <p className="text-xs text-red-600 dark:text-red-400">{wizardError}</p>}
              </div>
            )}

            <Row label="Endpoint" value={hwLabel} mono info="Ollama server URL (Docker container or native)" />
            <Row label="Active provider" value={isActive ? "Ollama (local, $0)" : "OpenRouter (cloud)"} info="Which LLM handles internal pipeline operations" />
          </>
        )}
        {/* Limitations */}
        <div className="rounded border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground leading-relaxed">
          <span className="font-medium">{ollamaReachable ? "Limitations:" : "What Ollama does:"}</span>{" "}
          {ollamaReachable
            ? "The local model handles classification, extraction, and routing well. Chat, verification, and synopsis generation always use cloud models via OpenRouter."
            : "Handles claim extraction, query decomposition, memory resolution, and topic extraction locally for $0. Chat and verification always use cloud models."}
        </div>
        {/* Pipeline Routing (Advanced mode only, Ollama reachable) */}
        {showPipelineRouting && pipelineProviders && (
          <>
            <div className="my-1 h-px bg-border" />
            <button
              type="button"
              className="flex w-full cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-muted/50"
              onClick={() => setPipelineOpen((v) => !v)}
              aria-expanded={pipelineOpen}
            >
              {pipelineOpen ? (
                <ChevronDown className="h-3 w-3 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-3 w-3 text-muted-foreground" />
              )}
              <span className="text-xs font-medium">Pipeline Routing</span>
              <InfoTip text="Shows which provider handles each pipeline stage. Configured via server environment variables." />
            </button>
            {pipelineOpen && (
              <div className="grid gap-1.5">
                {!ollamaReachable && (
                  <p className="mb-2 text-[11px] text-muted-foreground">
                    Install <a href="https://ollama.com" target="_blank" rel="noopener" className="text-primary underline">Ollama</a> to enable local pipeline routing.
                  </p>
                )}
                {Object.entries(STAGE_LABELS).map(([stage, label]) => {
                  const provider = pipelineProviders[stage as keyof typeof pipelineProviders] ?? "bifrost"
                  const isOllama = provider === "ollama"
                  const isLocked = LOCKED_STAGES.has(stage)
                  return (
                    <div key={stage} className="flex items-center justify-between px-1">
                      <span className="text-xs text-muted-foreground">{label}</span>
                      <span className="flex items-center gap-1.5">
                        {isLocked && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Lock className="h-3 w-3 text-muted-foreground/50" />
                            </TooltipTrigger>
                            <TooltipContent side="left" className="max-w-48">
                              <p>Always uses cloud models</p>
                            </TooltipContent>
                          </Tooltip>
                        )}
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px]",
                            isOllama
                              ? "bg-green-500/10 text-green-700 dark:text-green-400 border-green-500/30"
                              : "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30",
                          )}
                        >
                          {isOllama ? "Ollama" : "Cloud"}
                        </Badge>
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

/* ---- Watched Folders sub-component ---- */

function WatchedFoldersSection() {
  const { data, refetch } = useQuery({
    queryKey: ["watched-folders"],
    queryFn: fetchWatchedFolders,
    staleTime: 30_000,
  })
  const [addingPath, setAddingPath] = useState("")
  const [addingLabel, setAddingLabel] = useState("")
  const [showAdd, setShowAdd] = useState(false)
  const [actionId, setActionId] = useState<string | null>(null)
  const [folderError, setFolderError] = useState<string | null>(null)

  const handleAdd = async () => {
    if (!addingPath.trim()) return
    setActionId("adding")
    setFolderError(null)
    try {
      await addWatchedFolder({ path: addingPath.trim(), label: addingLabel.trim() || undefined })
      setAddingPath("")
      setAddingLabel("")
      setShowAdd(false)
      await refetch()
    } catch (e) {
      setFolderError(e instanceof Error ? e.message : "Failed to add folder")
    } finally {
      setActionId(null)
    }
  }

  const handleRemove = async (id: string) => {
    setActionId(id)
    try {
      await removeWatchedFolder(id)
      await refetch()
    } catch { /* non-critical */ }
    setActionId(null)
  }

  const handleToggle = async (folder: WatchedFolder, field: "enabled" | "search_enabled") => {
    setActionId(folder.id)
    try {
      await updateWatchedFolder(folder.id, { [field]: !folder[field] })
      await refetch()
    } catch { /* non-critical */ }
    setActionId(null)
  }

  const handleScan = async (id: string) => {
    setActionId(id)
    try {
      await scanWatchedFolder(id)
    } catch { /* non-critical */ }
    setActionId(null)
  }

  const folders = data?.folders ?? []

  return (
    <Card className="mb-4">
      <CardContent className="space-y-2 pt-4">
        <p className="text-[11px] text-muted-foreground">
          Directories monitored for automatic ingestion. Each folder can be scanned independently with domain overrides.
        </p>

        {folders.length === 0 && !showAdd && (
          <p className="text-xs text-muted-foreground/60 py-2">No watched folders configured.</p>
        )}

        {folders.map((folder) => (
          <div key={folder.id} className="rounded-lg border p-2.5 space-y-1.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 min-w-0">
                <FolderOpen className={cn("h-3.5 w-3.5 shrink-0", folder.enabled ? "text-brand" : "text-muted-foreground")} />
                <div className="min-w-0">
                  <p className="text-xs font-medium truncate">{folder.label}</p>
                  <p className="text-[10px] text-muted-foreground truncate font-mono">{folder.path}</p>
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => handleScan(folder.id)} disabled={actionId === folder.id}>
                      <Play className="h-3 w-3" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">Scan now</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-destructive" onClick={() => handleRemove(folder.id)} disabled={actionId === folder.id}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">Remove</TooltipContent>
                </Tooltip>
              </div>
            </div>
            <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
              <label className="flex items-center gap-1.5">
                <Switch checked={folder.enabled} onCheckedChange={() => handleToggle(folder, "enabled")} disabled={actionId === folder.id} className="scale-[0.6]" />
                <span>Active</span>
              </label>
              <label className="flex items-center gap-1.5">
                <Switch checked={folder.search_enabled} onCheckedChange={() => handleToggle(folder, "search_enabled")} disabled={actionId === folder.id} className="scale-[0.6]" />
                <span>Searchable</span>
              </label>
              {folder.domain_override && (
                <Badge variant="secondary" className="text-[9px]">{folder.domain_override}</Badge>
              )}
            </div>
            {folder.last_scanned_at && (
              <div className="flex gap-3 text-[10px] text-muted-foreground">
                <span>{folder.stats.ingested} ingested</span>
                <span>{folder.stats.skipped} skipped</span>
                {folder.stats.errored > 0 && <span className="text-red-500">{folder.stats.errored} errors</span>}
                <span>Last: {new Date(folder.last_scanned_at).toLocaleDateString()}</span>
              </div>
            )}
          </div>
        ))}

        {showAdd ? (
          <div className="rounded-lg border border-dashed border-muted-foreground/30 p-2.5 space-y-2">
            <input
              className="w-full rounded border bg-background px-2 py-1.5 text-xs font-mono"
              placeholder="/path/to/directory"
              value={addingPath}
              onChange={(e) => setAddingPath(e.target.value)}
            />
            <input
              className="w-full rounded border bg-background px-2 py-1.5 text-xs"
              placeholder="Label (optional)"
              value={addingLabel}
              onChange={(e) => setAddingLabel(e.target.value)}
            />
            <div className="flex gap-2">
              <Button size="sm" variant="default" className="h-7 text-xs" onClick={handleAdd} disabled={!addingPath.trim() || actionId === "adding"}>
                Add Folder
              </Button>
              <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => { setShowAdd(false); setAddingPath(""); setAddingLabel(""); setFolderError(null) }}>
                Cancel
              </Button>
            </div>
            {folderError && <p className="text-xs text-destructive">{folderError}</p>}
          </div>
        ) : (
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setShowAdd(true)}>
            <FolderOpen className="mr-1 h-3 w-3" />
            Add Folder
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

/* ---- Data Sources sub-component ---- */

function DataSourcesSection({
  sections,
  toggleSection,
}: {
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
}) {
  const { data: dataSources, refetch: refetchDS } = useQuery({
    queryKey: ["data-sources"],
    queryFn: fetchDataSources,
    staleTime: 30_000,
  })

  return (
    <>
      <SectionHeading icon={Globe} label="Data Sources" open={sections.data_sources ?? false} onToggle={() => toggleSection("data_sources" as SectionKey)} />
      {(sections.data_sources ?? false) && dataSources && (
        <Card className="mb-4">
          <CardContent className="space-y-2 pt-4">
            {dataSources.sources.length === 0 ? (
              <p className="text-sm text-muted-foreground">No data sources available.</p>
            ) : (
              dataSources.sources.map((source) => (
                <div key={source.name} className="flex items-center justify-between rounded-md border px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium capitalize">{source.name.replace(/_/g, " ")}</p>
                    <p className="text-[11px] text-muted-foreground">{source.description}</p>
                    {source.requires_api_key && !source.configured && (
                      <p className="mt-0.5 text-[10px] text-yellow-500">Set {source.api_key_env_var} in .env to enable</p>
                    )}
                  </div>
                  <Switch
                    size="sm"
                    checked={source.enabled && source.configured}
                    disabled={!source.configured}
                    onCheckedChange={(checked) => {
                      const op = checked ? enableDataSource : disableDataSource
                      op(source.name)
                        .then(() => refetchDS())
                        .catch((e) => console.warn("Data source toggle failed:", e))
                    }}
                  />
                </div>
              ))
            )}
          </CardContent>
        </Card>
      )}
    </>
  )
}

// -- Model Updates (auto-detection of new/deprecated models) -----------------

function ModelUpdatesSection() {
  const { data, isLoading } = useQuery<ModelUpdatesResponse>({
    queryKey: ["model-updates"],
    queryFn: fetchModelUpdates,
    staleTime: 5 * 60_000,
    refetchInterval: 10 * 60_000,
  })

  const hasNew = (data?.new?.length ?? 0) > 0
  const hasDeprecated = (data?.deprecated?.length ?? 0) > 0
  if (isLoading || (!hasNew && !hasDeprecated)) return null

  return (
    <Card className="mb-4">
      <CardHeader className="pb-2 pt-4 px-4">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <Sparkles className="h-4 w-4 text-muted-foreground" />
          Model Updates
          {hasNew && (
            <Badge variant="default" className="ml-auto text-[10px] px-1.5 py-0">
              {data!.new.length} new
            </Badge>
          )}
          {hasDeprecated && (
            <Badge variant="destructive" className="text-[10px] px-1.5 py-0">
              {data!.deprecated.length} deprecated
            </Badge>
          )}
        </CardTitle>
        {data?.last_checked && (
          <CardDescription className="text-[11px]">
            Last checked: {new Date(data.last_checked).toLocaleString()}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="grid gap-2 pt-0 px-4 pb-4">
        {hasNew && (
          <div>
            <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">New Models</p>
            <div className="space-y-1">
              {data!.new.slice(0, 10).map((m) => (
                <div key={m.id} className="flex items-center justify-between rounded border bg-muted/30 px-2 py-1.5">
                  <span className="text-xs font-mono truncate">{m.name ?? m.id}</span>
                  {m.context_length != null && (
                    <span className="ml-2 shrink-0 text-[10px] text-muted-foreground">
                      {Math.round(m.context_length / 1024)}K ctx
                    </span>
                  )}
                </div>
              ))}
              {data!.new.length > 10 && (
                <p className="text-[11px] text-muted-foreground">
                  +{data!.new.length - 10} more
                </p>
              )}
            </div>
          </div>
        )}
        {hasDeprecated && (
          <div>
            <p className="mb-1.5 flex items-center gap-1 text-[11px] font-medium text-destructive">
              <AlertTriangle className="h-3 w-3" />
              Deprecated Models
            </p>
            <div className="space-y-1">
              {data!.deprecated.map((m) => (
                <div key={m.id} className="rounded border border-destructive/30 bg-destructive/5 px-2 py-1.5">
                  <span className="text-xs font-mono">{m.id}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
