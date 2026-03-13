// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { fetchSettings, updateSettings, fetchKBStats, adminRebuildIndexes, adminRescore, adminRegenerateSummaries, adminClearDomain } from "@/lib/api"
import type { KBStats } from "@/lib/api"
import type { ServerSettings, SettingsUpdate } from "@/lib/types"
import { cn } from "@/lib/utils"
import { PRESETS, detectActivePreset } from "@/lib/settings-presets"
import { USER_PRESETS } from "@/lib/user-presets"
import { useUIMode } from "@/contexts/ui-mode-context"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Settings,
  Server,
  Database,
  Tag,
  ToggleLeft,
  Cpu,
  Loader2,
  AlertCircle,
  RefreshCw,
  Info,
  ChevronDown,
  ChevronRight,
  Wrench,
  SearchIcon,
  HardDrive,
  Trash2,
} from "lucide-react"
import { SyncSection } from "./sync-section"

type LoadState = "loading" | "error" | "ready"

type SectionKey = "connection" | "knowledge_ingestion" | "features" | "retrieval" | "search" | "taxonomy" | "infra_sync" | "kb_admin"

const SETTINGS_SECTIONS_VERSION = 2 // Bump to force new defaults on existing users

function readSectionState(): Record<SectionKey, boolean> {
  const defaults: Record<SectionKey, boolean> = {
    connection: true, knowledge_ingestion: true, features: true,
    retrieval: true, search: true, taxonomy: true, infra_sync: true,
    kb_admin: true,
  }
  try {
    const ver = localStorage.getItem("cerid-settings-sections-v")
    if (ver && parseInt(ver, 10) >= SETTINGS_SECTIONS_VERSION) {
      const raw = localStorage.getItem("cerid-settings-sections")
      if (raw) {
        const parsed = JSON.parse(raw) as Record<string, boolean>
        // Migrate: drop legacy keys, merge with new defaults
        return { ...defaults, ...Object.fromEntries(
          Object.entries(parsed).filter(([k]) => k in defaults)
        ) }
      }
    }
  } catch { /* noop */ }
  return defaults
}

function persistSectionState(state: Record<SectionKey, boolean>) {
  try {
    localStorage.setItem("cerid-settings-sections", JSON.stringify(state))
    localStorage.setItem("cerid-settings-sections-v", String(SETTINGS_SECTIONS_VERSION))
  } catch { /* noop */ }
}

function formatFlagName(flag: string): string {
  return flag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
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
  const [pipelineCustomize, setPipelineCustomize] = useState(false)
  const { mode: uiMode, setMode: setUIMode } = useUIMode()
  const [activeTab, setActiveTab] = useState<string>(() => {
    try { return localStorage.getItem("cerid-settings-tab") ?? "essentials" } catch { return "essentials" }
  })

  const handleTabChange = (tab: string) => {
    setActiveTab(tab)
    try { localStorage.setItem("cerid-settings-tab", tab) } catch { /* noop */ }
  }

  const applyUserPreset = async (preset: typeof USER_PRESETS[number]) => {
    setUIMode(preset.uiMode)
    // Apply localStorage overrides
    for (const [key, value] of Object.entries(preset.local)) {
      try { localStorage.setItem(key, value) } catch { /* noop */ }
    }
    // Apply server settings
    await patch(preset.settings)
  }

  const loadKBStats = useCallback(async () => {
    setKBLoading(true)
    try {
      const stats = await fetchKBStats()
      setKBStats(stats)
    } catch {
      /* ignore — section just stays empty */
    } finally {
      setKBLoading(false)
    }
  }, [])

  useEffect(() => {
    loadKBStats()
  }, [loadKBStats])

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
            {/* ── User Experience Presets ── */}
            <div className="grid grid-cols-3 gap-2">
              {USER_PRESETS.map((preset) => {
                // Detect active preset by comparing UI mode + key settings
                const isActive = uiMode === preset.uiMode &&
                  settings.enable_hallucination_check === (preset.settings.enable_hallucination_check ?? false) &&
                  settings.enable_feedback_loop === (preset.settings.enable_feedback_loop ?? false) &&
                  settings.enable_memory_extraction === (preset.settings.enable_memory_extraction ?? false)
                return (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => applyUserPreset(preset)}
                    className={cn(
                      "rounded-lg border p-3 text-left transition-colors",
                      isActive
                        ? "border-brand bg-brand/5"
                        : "border-muted hover:border-muted-foreground/30",
                    )}
                  >
                    <div className="flex items-center gap-1.5">
                      <span>{preset.emoji}</span>
                      <span className="text-sm font-medium">{preset.label}</span>
                    </div>
                    <p className="mt-1 text-[11px] leading-tight text-muted-foreground">
                      {preset.description}
                    </p>
                  </button>
                )
              })}
            </div>

            {/* ── Tabbed Settings ── */}
            <Tabs value={activeTab} onValueChange={handleTabChange}>
              <TabsList className="w-full">
                <TabsTrigger value="essentials" className="flex-1">Essentials</TabsTrigger>
                <TabsTrigger value="pipeline" className="flex-1">Pipeline</TabsTrigger>
                <TabsTrigger value="system" className="flex-1">System</TabsTrigger>
              </TabsList>

              {/* ── Essentials Tab ── */}
              <TabsContent value="essentials" className="space-y-1 pt-2">

            {/* ── Knowledge & Ingestion ── */}
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
                      min={0.5} max={1} step={0.05}
                      info="Minimum relevance score to auto-inject (higher = more selective)"
                    />
                  )}

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
                    info="Max tokens per chunk and overlap between chunks for embedding"
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

            {/* ── AI Features ── */}
            <SectionHeading icon={ToggleLeft} label="AI Features" open={sections.features} onToggle={() => toggleSection("features")} />
            {sections.features && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <ToggleRow
                    label="Self-RAG Validation"
                    enabled={settings.enable_self_rag ?? true}
                    onToggle={(v) => patch({ enable_self_rag: v })}
                    info="Validates retrieval quality and retries with refined queries if results are insufficient"
                  />
                  <ToggleRow
                    label="Feedback Loop"
                    enabled={settings.enable_feedback_loop}
                    onToggle={(v) => patch({ enable_feedback_loop: v })}
                    info="Saves AI responses back to your knowledge base for continuous improvement"
                  />
                  <ToggleRow
                    label="Hallucination Check"
                    enabled={settings.enable_hallucination_check}
                    onToggle={(v) => patch({ enable_hallucination_check: v })}
                    info="Verifies factual claims in AI responses against your knowledge base"
                  />
                  <ToggleRow
                    label="Memory Extraction"
                    enabled={settings.enable_memory_extraction}
                    onToggle={(v) => patch({ enable_memory_extraction: v })}
                    info="Extracts key facts and preferences from conversations into long-term memory"
                  />

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
                      value={settings.enable_model_router ? "recommend" : "manual"}
                      onValueChange={(v) => {
                        patch({ enable_model_router: v !== "manual" })
                        try { localStorage.setItem("cerid-routing-mode", v) } catch { /* noop */ }
                      }}
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

              </TabsContent>

              {/* ── Pipeline Tab ── */}
              <TabsContent value="pipeline" className="space-y-1 pt-2">

            {/* ── Retrieval Pipeline ── */}
            <SectionHeading icon={Cpu} label="Retrieval Pipeline" open={sections.retrieval} onToggle={() => toggleSection("retrieval")} />
            {sections.retrieval && (
              <Card className="mb-4">
                <CardHeader className="px-4 pb-2 pt-4">
                  <CardDescription className="text-xs">
                    Choose a preset or customize individual pipeline stages.
                  </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-4 px-4 pb-4">
                  {/* ── Preset cards ── */}
                  {(() => {
                    const activePreset = detectActivePreset(settings as unknown as Record<string, unknown>)
                    return (
                      <div className="grid grid-cols-3 gap-2">
                        {Object.entries(PRESETS).map(([key, preset]) => (
                          <button
                            key={key}
                            type="button"
                            onClick={() => patch(preset.values)}
                            className={cn(
                              "rounded-lg border p-2.5 text-left transition-colors",
                              activePreset === key
                                ? "border-primary bg-primary/5"
                                : "border-muted hover:border-muted-foreground/30",
                            )}
                          >
                            <span className="text-sm font-medium">{preset.label}</span>
                            <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">
                              {preset.description}
                            </p>
                          </button>
                        ))}
                      </div>
                    )
                  })()}

                  {!detectActivePreset(settings as unknown as Record<string, unknown>) && (
                    <p className="text-[11px] text-muted-foreground">
                      Custom configuration — doesn&apos;t match any preset
                    </p>
                  )}

                  {/* ── Customize disclosure ── */}
                  <button
                    type="button"
                    className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
                    onClick={() => setPipelineCustomize(!pipelineCustomize)}
                  >
                    {pipelineCustomize ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    Customize
                  </button>

                  {pipelineCustomize && (
                    <div className="space-y-4 border-t pt-4">
                  {/* Adaptive Retrieval */}
                  <PipelineToggle
                    label="Adaptive Retrieval"
                    enabled={settings.enable_adaptive_retrieval ?? false}
                    onToggle={(v) => patch({ enable_adaptive_retrieval: v })}
                    description="Classifies query complexity to skip or reduce retrieval for simple queries"
                  >
                    <SliderRow
                      label="Light Top-K"
                      value={settings.adaptive_retrieval_light_top_k ?? 3}
                      onChange={(v) => patch({ adaptive_retrieval_light_top_k: Math.round(v) })}
                      min={1} max={10} step={1}
                      info="Number of results for light retrieval mode"
                    />
                  </PipelineToggle>

                  <div className="h-px bg-border" />

                  {/* Query Decomposition */}
                  <PipelineToggle
                    label="Query Decomposition"
                    enabled={settings.enable_query_decomposition ?? false}
                    onToggle={(v) => patch({ enable_query_decomposition: v })}
                    description="Breaks complex multi-part questions into parallel sub-queries for broader coverage"
                  >
                    <SliderRow
                      label="Max Sub-queries"
                      value={settings.query_decomposition_max_subqueries ?? 4}
                      onChange={(v) => patch({ query_decomposition_max_subqueries: Math.round(v) })}
                      min={2} max={6} step={1}
                      info="Maximum number of sub-queries per decomposition"
                    />
                  </PipelineToggle>

                  <div className="h-px bg-border" />

                  {/* MMR Diversity */}
                  <PipelineToggle
                    label="MMR Diversity"
                    enabled={settings.enable_mmr_diversity ?? false}
                    onToggle={(v) => patch({ enable_mmr_diversity: v })}
                    description="Reorders results using Maximal Marginal Relevance for diverse, non-redundant context"
                  >
                    <SliderRow
                      label="Lambda"
                      value={settings.mmr_lambda ?? 0.7}
                      onChange={(v) => patch({ mmr_lambda: v })}
                      min={0} max={1} step={0.05}
                      info="Balance between relevance (1.0) and diversity (0.0)"
                    />
                  </PipelineToggle>

                  <div className="h-px bg-border" />

                  {/* Intelligent Assembly */}
                  <PipelineToggle
                    label="Intelligent Assembly"
                    enabled={settings.enable_intelligent_assembly ?? false}
                    onToggle={(v) => patch({ enable_intelligent_assembly: v })}
                    description="Three-pass context assembly maximizing query facet coverage"
                  />

                  <div className="h-px bg-border" />

                  {/* Late Interaction */}
                  <PipelineToggle
                    label="Late Interaction"
                    enabled={settings.enable_late_interaction ?? false}
                    onToggle={(v) => patch({ enable_late_interaction: v })}
                    description="ColBERT-inspired MaxSim scoring for fine-grained token-level relevance"
                  >
                    <SliderRow
                      label="Top-N Candidates"
                      value={settings.late_interaction_top_n ?? 8}
                      onChange={(v) => patch({ late_interaction_top_n: Math.round(v) })}
                      min={4} max={16} step={1}
                      info="Number of candidates for late interaction scoring"
                    />
                    <SliderRow
                      label="Blend Weight"
                      value={settings.late_interaction_blend_weight ?? 0.15}
                      onChange={(v) => patch({ late_interaction_blend_weight: v })}
                      min={0} max={0.5} step={0.05}
                      info="Weight of late interaction score blended into final ranking"
                    />
                  </PipelineToggle>

                  <div className="h-px bg-border" />

                  {/* Semantic Cache */}
                  <PipelineToggle
                    label="Semantic Cache"
                    enabled={settings.enable_semantic_cache ?? false}
                    onToggle={(v) => patch({ enable_semantic_cache: v })}
                    description="Caches retrieval results keyed by semantic query similarity"
                  >
                    <SliderRow
                      label="Similarity Threshold"
                      value={settings.semantic_cache_threshold ?? 0.92}
                      onChange={(v) => patch({ semantic_cache_threshold: v })}
                      min={0.8} max={1} step={0.01}
                      info="Minimum cosine similarity for a cache hit"
                    />
                  </PipelineToggle>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {/* ── Search Tuning ── */}
            <SectionHeading icon={SearchIcon} label="Search Tuning" open={sections.search} onToggle={() => toggleSection("search")} />
            {sections.search && (
              <Card className="mb-4">
                <CardContent className="grid gap-4 pt-4">
                  <SliderRow
                    label="Vector Weight"
                    value={settings.hybrid_vector_weight ?? 0.6}
                    onChange={(v) => patch({ hybrid_vector_weight: v })}
                    min={0} max={1} step={0.05}
                    info="Weight for vector similarity in hybrid search (0-1)"
                  />
                  <SliderRow
                    label="Keyword Weight"
                    value={settings.hybrid_keyword_weight ?? 0.4}
                    onChange={(v) => patch({ hybrid_keyword_weight: v })}
                    min={0} max={1} step={0.05}
                    info="Weight for BM25 keyword matching in hybrid search (0-1)"
                  />

                  <div className="my-1 h-px bg-border" />

                  <SliderRow
                    label="Rerank LLM Weight"
                    value={settings.rerank_llm_weight ?? 0.6}
                    onChange={(v) => patch({ rerank_llm_weight: v })}
                    min={0} max={1} step={0.05}
                    info="Weight for LLM-based reranking score (0-1)"
                  />
                  <SliderRow
                    label="Rerank Original Weight"
                    value={settings.rerank_original_weight ?? 0.4}
                    onChange={(v) => patch({ rerank_original_weight: v })}
                    min={0} max={1} step={0.05}
                    info="Weight for original relevance score in reranking (0-1)"
                  />

                  <div className="my-1 h-px bg-border" />

                  <Row
                    label="Temporal Half-life"
                    value={settings.temporal_half_life_days ? `${settings.temporal_half_life_days} days` : "—"}
                    info="Days until temporal recency boost decays by half"
                  />
                  <Row
                    label="Recency Weight"
                    value={(settings.temporal_recency_weight ?? 0.1).toFixed(2)}
                    info="Maximum boost from document recency"
                  />
                </CardContent>
              </Card>
            )}

              </TabsContent>

              {/* ── System Tab ── */}
              <TabsContent value="system" className="space-y-1 pt-2">

            {/* ── Connection ── */}
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

            {/* ── Taxonomy ── */}
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

            {/* ── Infrastructure & Sync ── */}
            <SectionHeading icon={Wrench} label="Infrastructure & Sync" open={sections.infra_sync} onToggle={() => toggleSection("infra_sync")} />
            {sections.infra_sync && (
              <>
                <Card className="mb-2">
                  <CardContent className="grid gap-3 pt-4">
                    <Row label="Bifrost URL" value={settings.bifrost_url ?? "—"} mono info="LLM gateway endpoint" />
                    <Row label="Bifrost Timeout" value={settings.bifrost_timeout ? `${settings.bifrost_timeout}s` : "—"} info="Request timeout for LLM calls" />
                    <Row label="ChromaDB" value={settings.chroma_url ?? "—"} mono info="Vector database endpoint" />
                    <Row label="Neo4j" value={settings.neo4j_uri ?? "—"} mono info="Graph database endpoint" />
                    <Row label="Redis" value={settings.redis_url ?? "—"} mono info="Cache and BM25 index (password redacted)" />
                    <Row label="Archive Path" value={settings.archive_path ?? "—"} mono info="File archive mount path" />
                    <Row label="Chunking Mode" value={settings.chunking_mode ?? "—"} info="Token-based or semantic chunking" />
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

            {/* ── KB Management ── */}
            <SectionHeading icon={HardDrive} label="KB Management" open={sections.kb_admin} onToggle={() => toggleSection("kb_admin")} />
            {sections.kb_admin && (
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
            )}

              </TabsContent>
            </Tabs>
          </div>
        </TooltipProvider>
      </ScrollArea>
    </div>
  )
}

/* ── Helper Components ── */

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

function SectionHeading({
  icon: Icon,
  label,
  open,
  onToggle,
}: {
  icon: typeof Cpu
  label: string
  open: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      className="mb-2 flex w-full cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-muted/50"
      onClick={onToggle}
      aria-expanded={open}
    >
      {open ? (
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      ) : (
        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
      )}
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h3 className="text-sm font-medium">{label}</h3>
    </button>
  )
}

function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Info className="h-3.5 w-3.5 shrink-0 cursor-help text-muted-foreground/50 hover:text-muted-foreground" />
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p>{text}</p>
      </TooltipContent>
    </Tooltip>
  )
}

function LabelWithInfo({ label, info }: { label: string; info: string }) {
  return (
    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
      {label}
      <InfoTip text={info} />
    </span>
  )
}

function Row({ label, value, mono, info }: { label: string; value: string; mono?: boolean; info?: string }) {
  return (
    <div className="flex items-center justify-between">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <span className={cn("text-sm", mono && "font-mono text-xs")}>{value}</span>
    </div>
  )
}

function ToggleRow({
  label,
  enabled,
  onToggle,
  info,
}: {
  label: string
  enabled: boolean
  onToggle: (value: boolean) => void
  info?: string
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <Switch size="sm" checked={enabled} onCheckedChange={onToggle} />
    </div>
  )
}

function SliderRow({
  label,
  value,
  onChange,
  min,
  max,
  step,
  info,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step: number
  info?: string
}) {
  const display = step >= 1 ? String(value) : value.toFixed(2)
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex min-w-0 items-center gap-1.5">
        <LabelWithInfo
          label={`${label}: ${display}`}
          info={info ?? label}
        />
      </div>
      <Slider
        value={[value]}
        onValueChange={([v]) => onChange(v)}
        min={min}
        max={max}
        step={step}
        className="w-32"
        aria-label={label}
      />
    </div>
  )
}

function PipelineToggle({
  label,
  enabled,
  onToggle,
  description,
  children,
}: {
  label: string
  enabled: boolean
  onToggle: (value: boolean) => void
  description: string
  children?: React.ReactNode
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium">{label}</span>
          <span className="text-[11px] leading-tight text-muted-foreground">{description}</span>
        </div>
        <Switch size="sm" checked={enabled} onCheckedChange={onToggle} />
      </div>
      {enabled && children && (
        <div className="ml-4 space-y-2 border-l-2 border-muted pl-3">
          {children}
        </div>
      )}
    </div>
  )
}
