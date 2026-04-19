// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import type { ServerSettings, SettingsUpdate, RoutingMode, ProviderCredits } from "@/lib/types"
import type { SectionKey } from "./settings-primitives"
import { useSettings } from "@/hooks/use-settings"
import { cn } from "@/lib/utils"
import { Card, CardContent } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Database, ToggleLeft, CreditCard, ExternalLink, Globe, Shield, AlertTriangle, Cpu, Zap } from "lucide-react"
import { fetchDataSources, enableDataSource, disableDataSource, fetchSetupStatus, fetchHealthStatus, fetchSystemCheck } from "@/lib/api"
import { SectionHeading, LabelWithInfo, Row, ToggleRow, SliderRow } from "./settings-primitives"
import { assessRuntime, fromHealthStatus, CAPABILITY_STATUS_DOT, COST_PROFILE_LABELS } from "@/lib/provider-capabilities"
import { OpenRouterKeyField } from "./openrouter-key-field"

/** Shows hardware-based recommendation for the current UI mode. */
function HardwareRecommendation() {
  const { data: hw } = useQuery({
    queryKey: ["system-check"],
    queryFn: fetchSystemCheck,
    staleTime: 60_000,
    retry: 1,
  })

  if (!hw || hw.ram_gb === 0) return null

  const gpuAvailable = hw.gpu_acceleration !== "none"
  const capable = hw.ram_gb >= 16 || gpuAvailable

  return (
    <div className="mb-4 rounded-lg border bg-card px-3 py-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium">System</span>
        <span className="text-[10px] text-muted-foreground ml-auto">
          {hw.ram_gb}GB RAM · {hw.cpu_cores ?? "?"} cores
          {gpuAvailable && ` · ${hw.gpu_acceleration}`}
        </span>
      </div>
      <p className="text-[10px] text-muted-foreground">
        {capable
          ? "Your hardware supports all features — Advanced mode recommended for full verification, reranking, and smart routing."
          : "Simple mode recommended for smooth performance on your hardware. Switch to Advanced anytime if needed."}
      </p>
      {gpuAvailable && (
        <div className="mt-1.5 flex items-center gap-1">
          <Zap className="h-2.5 w-2.5 text-green-500" />
          <span className="text-[9px] text-green-600 dark:text-green-400">
            GPU acceleration active — embeddings and reranking use {hw.gpu_acceleration}
          </span>
        </div>
      )}
    </div>
  )
}

interface EssentialsSectionProps {
  settings: ServerSettings
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
  patch: (update: SettingsUpdate) => Promise<void>
  credits?: ProviderCredits
}

export function EssentialsSection({ settings, sections, toggleSection, patch, credits }: EssentialsSectionProps) {
  const { routingMode, setRoutingMode } = useSettings()
  const { data: healthStatus } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
    retry: 1,
    staleTime: 10_000,
  })
  const canVerify = healthStatus?.can_verify ?? true
  const canRetrieve = healthStatus?.can_retrieve ?? true
  const canGenerate = healthStatus?.can_generate ?? true

  return (
    <>
      {/* -- Provider Credits -- */}
      <SectionHeading icon={CreditCard} label="Provider Credits" open={sections.credits} onToggle={() => toggleSection("credits")} />
      {sections.credits && (
        <Card className="mb-4">
          <CardContent className="grid gap-3 pt-4">
            {credits?.configured ? (
              <>
                <div className="flex items-center justify-between">
                  <LabelWithInfo label="Balance" info="Remaining OpenRouter credits" />
                  <span className={cn(
                    "text-sm font-semibold tabular-nums",
                    credits.status === "ok" && "text-green-600 dark:text-green-400",
                    credits.status === "low" && "text-yellow-600 dark:text-yellow-400",
                    credits.status === "exhausted" && "text-red-600 dark:text-red-400",
                    credits.status === "error" && "text-muted-foreground",
                  )}>
                    ${credits.balance?.toFixed(2) ?? "\u2014"}
                  </span>
                </div>
                {credits.warning && (
                  <p className="text-xs text-yellow-600 dark:text-yellow-400">{credits.warning}</p>
                )}
                <div className="my-1 h-px bg-border" />
                <Row label="Today" value={credits.usage_daily != null ? `$${credits.usage_daily.toFixed(4)}` : "\u2014"} info="Spend today" />
                <Row label="This Month" value={credits.usage_monthly != null ? `$${credits.usage_monthly.toFixed(2)}` : "\u2014"} info="Spend this month" />
                <div className="my-1 h-px bg-border" />
                <div className="flex gap-2">
                  <a
                    href={credits.top_up_url ?? "https://openrouter.ai/settings/credits"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-brand px-3 text-xs font-medium text-brand-foreground hover:bg-brand/90"
                  >
                    Add Credits
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                No OpenRouter API key configured.
              </p>
            )}
            <div className="my-1 h-px bg-border" />
            <OpenRouterKeyField />
          </CardContent>
        </Card>
      )}

      {/* -- Provider Status -- */}
      <SectionHeading icon={Shield} label="Provider Status" open={sections.provider_status} onToggle={() => toggleSection("provider_status")} />
      {sections.provider_status && (
        <ProviderStatusPanel settings={settings} />
      )}

      {/* -- System Recommendation -- */}
      <HardwareRecommendation />

      {/* -- Knowledge & Ingestion -- */}
      <SectionHeading icon={Database} label="Knowledge & Ingestion" open={sections.knowledge_ingestion} onToggle={() => toggleSection("knowledge_ingestion")} />
      {sections.knowledge_ingestion && (
        <Card className="mb-4">
          <CardContent className="grid gap-4 pt-4">
            <ToggleRow
              label="Auto-inject KB Context"
              enabled={settings.enable_auto_inject}
              onToggle={(v) => patch({ enable_auto_inject: v })}
              info="Automatically includes relevant KB context when relevance exceeds threshold"
            />
            {settings.enable_auto_inject && (
              <SliderRow
                label="Injection Threshold"
                value={settings.auto_inject_threshold}
                onChange={(v) => patch({ auto_inject_threshold: v })}
                min={0.05} max={0.5} step={0.05}
                info="Minimum relevance score to auto-inject (higher = more selective)"
              />
            )}

            <div className="flex items-center justify-between">
              <LabelWithInfo label="KB Injection Mode" info="When to include KB context in queries (Smart detects relevance, Always includes context)" />
              <Select
                value={settings.rag_mode ?? "smart"}
                onValueChange={(v) => patch({ rag_mode: v })}
              >
                <SelectTrigger size="sm" className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="smart">Smart (auto-detect)</SelectItem>
                  <SelectItem value="always">Always inject KB</SelectItem>
                  <SelectItem value="manual">Manual only</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="my-1 h-px bg-border" />

            <div className="flex items-center justify-between">
              <LabelWithInfo label="Categorization Mode" info="How uploaded documents are categorized into KB domains" />
              <Select
                value={settings.categorize_mode}
                onValueChange={(v) => patch({ categorize_mode: v })}
              >
                <SelectTrigger size="sm" className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="smart">Smart</SelectItem>
                  <SelectItem value="pro">Pro</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Row
              label="Chunk Size"
              value={`${settings.chunk_max_tokens} tokens / ${settings.chunk_overlap} overlap`}
              info="Chunk size: how many tokens per searchable segment (larger = more context per result, fewer results). Overlap: how much adjacent chunks share (higher = better context continuity, more storage). Recommended: 400-512 tokens, 15-25% overlap."
            />
            <div className="flex items-center justify-between">
              <LabelWithInfo label="Storage Mode" info="Extract-only parses text and discards the file. Archive keeps a copy in the sync directory." />
              <Select
                value={settings.storage_mode}
                onValueChange={(v) => patch({ storage_mode: v })}
              >
                <SelectTrigger size="sm" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="extract_only">Extract Only</SelectItem>
                  <SelectItem value="archive">Archive</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <ToggleRow
              label="Contextual Chunking"
              enabled={settings.enable_contextual_chunks ?? false}
              onToggle={(v) => patch({ enable_contextual_chunks: v })}
              info="Adds LLM-generated situational summaries to each chunk for richer retrieval context"
            />
          </CardContent>
        </Card>
      )}

      {/* -- AI Features -- */}
      <SectionHeading icon={ToggleLeft} label="AI Features" open={sections.features} onToggle={() => toggleSection("features")} />
      {sections.features && (
        <Card className="mb-4">
          <CardContent className="grid gap-3 pt-4">
            <div>
              <ToggleRow
                label="Self-RAG Validation"
                enabled={settings.enable_self_rag ?? true}
                onToggle={(v) => patch({ enable_self_rag: v })}
                info="Validates retrieval quality and retries with refined queries if results are insufficient"
              />
              {!canRetrieve && (settings.enable_self_rag ?? true) && (
                <DegradedFeatureNote message="Retrieval services temporarily unavailable" command="docker restart ai-companion-chroma ai-companion-neo4j" />
              )}
            </div>
            <div>
              <ToggleRow
                label="Feedback Loop"
                enabled={settings.enable_feedback_loop}
                onToggle={(v) => patch({ enable_feedback_loop: v })}
                info="Saves AI responses back to your knowledge base for continuous improvement"
              />
              {!canGenerate && settings.enable_feedback_loop && (
                <DegradedFeatureNote message="AI generation temporarily unavailable" command="docker logs ai-companion-mcp --tail 20" />
              )}
            </div>
            <div>
              <ToggleRow
                label="Hallucination Check"
                enabled={settings.enable_hallucination_check}
                onToggle={(v) => patch({ enable_hallucination_check: v })}
                info="Verifies factual claims in AI responses against your knowledge base"
              />
              {!canVerify && settings.enable_hallucination_check && (
                <DegradedFeatureNote message="Verification services temporarily unavailable" command="docker logs ai-companion-mcp --tail 20" />
              )}
            </div>
            <div>
              <ToggleRow
                label="Memory Extraction"
                enabled={settings.enable_memory_extraction}
                onToggle={(v) => patch({ enable_memory_extraction: v })}
                info="Extracts key facts and preferences from conversations into long-term memory"
              />
              {!canGenerate && settings.enable_memory_extraction && (
                <DegradedFeatureNote message="AI generation temporarily unavailable" command="docker logs ai-companion-mcp --tail 20" />
              )}
            </div>

            <div className="my-1 h-px bg-border" />

            <SliderRow
              label="Hallucination Threshold"
              value={settings.hallucination_threshold}
              onChange={(v) => patch({ hallucination_threshold: v })}
              min={0} max={1} step={0.05}
              info="Confidence threshold for flagging claims (lower = more sensitive)"
            />

            <div className="my-1 h-px bg-border" />

            <div className="flex items-center justify-between">
              <LabelWithInfo label="Model Router" info="Manual: no suggestions. Recommend: shows switch banner. Auto: silently picks the best model." />
              <Select
                value={routingMode}
                onValueChange={(v) => setRoutingMode(v as RoutingMode)}
              >
                <SelectTrigger size="sm" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="recommend">Recommend</SelectItem>
                  <SelectItem value="auto">Auto</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center justify-between">
              <LabelWithInfo label="Cost Sensitivity" info="How aggressively the model router optimizes for cost vs quality" />
              <Select
                value={settings.cost_sensitivity ?? "medium"}
                onValueChange={(v) => patch({ cost_sensitivity: v })}
              >
                <SelectTrigger size="sm" className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>
      )}

      {/* -- External Data Sources -- */}
      <SectionHeading icon={Globe} label="External Data Sources" open={sections.data_sources} onToggle={() => toggleSection("data_sources")} />
      {sections.data_sources && <DataSourcesPanel />}
    </>
  )
}


function DegradedFeatureNote({ message, command }: { message: string; command?: string }) {
  return (
    <div className="mt-1 rounded bg-yellow-500/10 px-2.5 py-1" role="status">
      <div className="flex items-center gap-1.5">
        <AlertTriangle className="h-3 w-3 shrink-0 text-yellow-600 dark:text-yellow-400" />
        <span className="text-[10px] text-yellow-600 dark:text-yellow-400">{message}</span>
      </div>
      {command && (
        <code className="mt-1 block rounded bg-background/60 px-2 py-0.5 font-mono text-[9px] text-muted-foreground">
          {command}
        </code>
      )}
    </div>
  )
}

function ProviderStatusPanel({ settings }: { settings: ServerSettings }) {
  const { data: setupStatus } = useQuery({
    queryKey: ["setup-status"],
    queryFn: fetchSetupStatus,
    staleTime: 60_000,
  })
  const { data: healthStatus } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
    retry: 1,
    staleTime: 10_000,
  })

  const configuredProviders = setupStatus?.configured_providers ?? []
  const ollamaEnabled = settings.ollama_enabled ?? false
  const assessment = assessRuntime(
    fromHealthStatus(healthStatus ?? {}, configuredProviders, ollamaEnabled),
  )

  const ALL_PROVIDERS = [
    { id: "openrouter", label: "OpenRouter" },
    { id: "openai", label: "OpenAI" },
    { id: "anthropic", label: "Anthropic" },
    { id: "xai", label: "xAI (Grok)" },
  ]

  return (
    <Card className="mb-4">
      <CardContent className="grid gap-3 pt-4">
        {/* Provider badges */}
        <div className="flex flex-wrap gap-1.5">
          {ALL_PROVIDERS.map((p) => {
            const configured = configuredProviders.includes(p.id)
            return (
              <div
                key={p.id}
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]",
                  configured
                    ? "border-green-500/30 bg-green-500/5 text-green-600 dark:text-green-400"
                    : "border-muted text-muted-foreground",
                )}
              >
                <span className={cn("h-1.5 w-1.5 rounded-full", configured ? "bg-green-500" : "bg-muted-foreground/30")} />
                {p.label}
              </div>
            )
          })}
          <div
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]",
              ollamaEnabled
                ? "border-green-500/30 bg-green-500/5 text-green-600 dark:text-green-400"
                : "border-muted text-muted-foreground",
            )}
          >
            <span className={cn("h-1.5 w-1.5 rounded-full", ollamaEnabled ? "bg-green-500" : "bg-muted-foreground/30")} />
            Ollama
          </div>
        </div>

        {/* Capability grid */}
        <div className="rounded border bg-muted/30 p-2.5">
          <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Capabilities
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            {assessment.capabilities.map((cap) => (
              <div key={cap.label}>
                <div className="flex items-center gap-1.5">
                  <div className={cn("h-1.5 w-1.5 rounded-full shrink-0", CAPABILITY_STATUS_DOT[cap.status])} />
                  <span className="text-[11px] text-muted-foreground">{cap.label}</span>
                </div>
                {cap.status !== "available" && cap.reason && (
                  <p className="ml-3 text-[9px] text-muted-foreground/70">{cap.reason}</p>
                )}
                {cap.fix && cap.status !== "available" && cap.fix.command && (
                  <code className="ml-3 mt-0.5 block text-[8px] font-mono text-muted-foreground/60">
                    {cap.fix.command}
                  </code>
                )}
              </div>
            ))}
          </div>
          <p className="mt-1.5 text-[10px] text-muted-foreground/70">
            {COST_PROFILE_LABELS[assessment.costProfile]}
          </p>
        </div>

        {/* Warnings */}
        {assessment.warnings.length > 0 && (
          <div className="space-y-1.5">
            {assessment.warnings.map((w, i) => (
              <div key={i}>
                <p className={cn(
                  "text-[11px] leading-relaxed",
                  w.severity === "error" && "text-destructive",
                  w.severity === "warning" && "text-yellow-600 dark:text-yellow-400",
                  w.severity === "info" && "text-muted-foreground",
                )}>
                  {w.message}
                </p>
                {w.fix?.command && (
                  <code className="mt-0.5 block rounded bg-background/60 px-2 py-0.5 text-[9px] font-mono text-muted-foreground">
                    {w.fix.command}
                  </code>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function DataSourcesPanel() {
  const { data, refetch } = useQuery({
    queryKey: ["data-sources"],
    queryFn: fetchDataSources,
    staleTime: 30_000,
  })
  const [toggling, setToggling] = useState<string | null>(null)

  const handleToggle = async (name: string, currentlyEnabled: boolean) => {
    setToggling(name)
    try {
      if (currentlyEnabled) {
        await disableDataSource(name)
      } else {
        await enableDataSource(name)
      }
      await refetch()
    } catch (e) {
      console.warn("Data source toggle failed:", e)
    } finally {
      setToggling(null)
    }
  }

  if (!data) return null

  const enabledCount = data.sources.filter((s) => s.enabled && s.configured).length

  return (
    <Card className="mb-4">
      <CardContent className="grid gap-2 pt-4">
        <p className="text-[11px] text-muted-foreground">
          {enabledCount} of {data.sources.length} sources active. External APIs enrich RAG results when KB has no relevant matches.
        </p>
        {data.sources.map((src) => (
          <div key={src.name} className="flex items-center justify-between rounded-md border px-3 py-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className={cn("h-2 w-2 rounded-full shrink-0", src.enabled && src.configured ? "bg-green-500" : "bg-muted-foreground/30")} />
              <div className="min-w-0">
                <p className="text-xs font-medium capitalize">{src.name.replace(/_/g, " ")}</p>
                <p className="text-[10px] text-muted-foreground truncate">{src.description}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0 ml-2">
              {src.requires_api_key && !src.configured && (
                <Badge variant="outline" className="text-[9px] text-amber-600 dark:text-yellow-400 border-yellow-500/30">
                  Key required
                </Badge>
              )}
              {src.domains.length > 0 && (
                <Badge variant="secondary" className="text-[9px]">
                  {src.domains.join(", ")}
                </Badge>
              )}
              <Switch
                checked={src.enabled}
                onCheckedChange={() => handleToggle(src.name, src.enabled)}
                disabled={toggling === src.name || (src.requires_api_key && !src.configured)}
                className="scale-75"
              />
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
