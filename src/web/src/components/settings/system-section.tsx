// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { adminRebuildIndexes, adminRescore, adminRegenerateSummaries, adminClearDomain, fetchOllamaStatus, enableOllama, disableOllama, pullOllamaModel, fetchHealthStatus, fetchDataSources, enableDataSource, disableDataSource } from "@/lib/api"
import type { KBStats } from "@/lib/api"
import type { ServerSettings, SettingsUpdate, ProviderCredits, OllamaStatus, HealthStatusResponse } from "@/lib/types"
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
} from "lucide-react"
import { SyncSection } from "./sync-section"
import { SectionHeading, InfoTip, LabelWithInfo, Row } from "./settings-primitives"

function formatFlagName(flag: string): string {
  return flag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
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

      {/* -- Taxonomy -- */}
      <SectionHeading icon={Tag} label="Taxonomy" open={sections.taxonomy} onToggle={() => toggleSection("taxonomy")} />
      {sections.taxonomy && (
        <div className="mb-4 grid gap-3">
          {Object.entries(settings.taxonomy).map(([domain, info]) => (
            <Card key={domain}>
              <CardHeader className="p-4 pb-2">
                <div className="flex items-center gap-2">
                  <span className="text-base">{info.icon}</span>
                  <CardTitle className="text-sm capitalize">{domain}</CardTitle>
                </div>
                <CardDescription className="text-xs">{info.description}</CardDescription>
              </CardHeader>
              {info.sub_categories.length > 0 && (
                <CardContent className="flex flex-wrap gap-1.5 px-4 pb-3 pt-0">
                  {info.sub_categories.map((sub) => (
                    <Badge key={sub} variant="outline" className="text-[10px]">
                      {sub}
                    </Badge>
                  ))}
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      )}

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
  // Wizard state: null (idle), "install" (show instructions), "polling" (waiting for Ollama), "pulling" (model download), "enabling", "complete"
  const [wizardPhase, setWizardPhase] = useState<string | null>(null)
  const [wizardError, setWizardError] = useState<string | null>(null)
  const [pullProgress, setPullProgress] = useState("")
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

  // Poll for Ollama becoming reachable after user installs it
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
          // Auto-advance: pull model if needed, then enable
          if (!status.default_model_installed) {
            const model = status.default_model || "llama3.2:3b"
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
                  // Parse progress from Ollama NDJSON stream
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
          }
          // Enable
          setWizardPhase("enabling")
          try {
            await enableOllama()
            await refetchOllama()
            onRefresh()
            setWizardPhase("complete")
            setTimeout(() => setWizardPhase(null), 4000)
          } catch (e) {
            setWizardPhase(null)
            setWizardError(e instanceof Error ? e.message : "Enable failed")
          }
        }
      } catch { /* polling error — keep trying */ }
    }, 3000)
  }, [refetchOllama, onRefresh])

  // Cleanup polling on unmount
  useEffect(() => () => { if (pollingRef.current) clearInterval(pollingRef.current) }, [])

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
          Uses llama3.2:3b (~2GB) by default. Falls back to OpenRouter when unavailable.
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

        {/* Setup wizard — shown when Ollama is not reachable */}
        {!ollamaReachable && !isActive && (
          <div className="rounded-lg border border-dashed border-muted-foreground/30 p-3 space-y-3">
            {wizardPhase === "complete" ? (
              <div className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400">
                <CheckCircle2 className="h-4 w-4" />
                <span className="font-medium">Ollama enabled successfully! Pipeline tasks now run locally for free.</span>
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
                <p className="text-[11px] text-muted-foreground">Copy the command for your OS, paste it in your terminal, then click continue. Setup will auto-detect Ollama and finish configuration.</p>
                <div className="space-y-1.5">
                  {[
                    { os: "macOS", cmd: "brew install ollama && ollama serve" },
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
            <Row label="Model" value={settings.internal_llm_model ?? "llama3.2:3b"} mono info="Lightweight model for pipeline intelligence tasks" />
            {ollamaReachable && !ollamaStatus?.default_model_installed && (
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Default model</span>
                <Button size="sm" variant="outline" className="h-6 text-[10px]" onClick={startPolling} disabled={!!wizardPhase}>
                  <Download className="mr-1 h-2.5 w-2.5" />
                  Pull {ollamaStatus?.default_model ?? "llama3.2:3b"}
                </Button>
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

/* ---- Data Sources sub-component ---- */

function DataSourcesSection({
  sections,
  toggleSection,
}: {
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
}) {
  const { data: dataSources } = useQuery({
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
                      if (checked) {
                        enableDataSource(source.name).catch(() => {})
                      } else {
                        disableDataSource(source.name).catch(() => {})
                      }
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
