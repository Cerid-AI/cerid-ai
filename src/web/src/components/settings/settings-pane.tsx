// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { fetchSettings, updateSettings, fetchKBStats, adminRebuildIndexes, adminRescore, adminRegenerateSummaries, adminClearDomain } from "@/lib/api"
import type { KBStats } from "@/lib/api"
import type { ServerSettings, SettingsUpdate } from "@/lib/types"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
  Shield,
  Tag,
  ToggleLeft,
  Cpu,
  Layers,
  Loader2,
  AlertCircle,
  RefreshCw,
  Info,
  ChevronDown,
  ChevronRight,
  FolderSync,
  Wrench,
  SearchIcon,
  HardDrive,
  Trash2,
} from "lucide-react"
import { SyncSection } from "./sync-section"

type LoadState = "loading" | "error" | "ready"

type SectionKey = "connection" | "ingestion" | "features" | "knowledge" | "taxonomy" | "encryption" | "infrastructure" | "search" | "sync" | "kb_admin" | "flags"

function readSectionState(): Record<SectionKey, boolean> {
  const defaults: Record<SectionKey, boolean> = {
    connection: true, ingestion: true, features: true, knowledge: true,
    taxonomy: true, encryption: true, infrastructure: false, search: false, sync: true, kb_admin: true, flags: true,
  }
  try {
    const raw = localStorage.getItem("cerid-settings-sections")
    if (raw) return { ...defaults, ...JSON.parse(raw) }
  } catch { /* noop */ }
  return defaults
}

function persistSectionState(state: Record<SectionKey, boolean>) {
  try { localStorage.setItem("cerid-settings-sections", JSON.stringify(state)) } catch { /* noop */ }
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
      // Invalidate KB-related caches so other panes pick up updated scores/data
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
          <div className="space-y-1 p-4">
            {/* Connection */}
            <SectionHeading icon={Server} label="Connection" open={sections.connection} onToggle={() => toggleSection("connection")} />
            {sections.connection && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <Row label="Server Version" value={settings.version} info="Current MCP server version" />
                  <Row label="Machine ID" value={settings.machine_id} mono info="Unique identifier for this server instance" />
                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Feature Tier" info="Community: taxonomy, file upload, encryption, truth audit, live metrics. Pro: OCR, audio transcription, image understanding, semantic dedup, advanced analytics, multi-user. Set via CERID_TIER env var." />
                    <Badge variant={settings.feature_tier === "pro" ? "default" : "secondary"}>
                      {settings.feature_tier}
                    </Badge>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Ingestion */}
            <SectionHeading icon={Database} label="Ingestion" open={sections.ingestion} onToggle={() => toggleSection("ingestion")} />
            {sections.ingestion && (
              <Card className="mb-4">
                <CardContent className="grid gap-4 pt-4">
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
                </CardContent>
              </Card>
            )}

            {/* Features */}
            <SectionHeading icon={ToggleLeft} label="Features" open={sections.features} onToggle={() => toggleSection("features")} />
            {sections.features && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
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

                  <div className="my-1 h-px bg-border" />

                  <div className="flex items-center justify-between gap-4">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <LabelWithInfo
                        label={`Hallucination Threshold: ${settings.hallucination_threshold.toFixed(2)}`}
                        info="Confidence threshold for flagging claims (lower = more sensitive)"
                      />
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={settings.hallucination_threshold}
                      onChange={(e) =>
                        patch({ hallucination_threshold: parseFloat(e.target.value) })
                      }
                      className="h-1.5 w-32 cursor-pointer accent-primary"
                      aria-label="Hallucination threshold"
                    />
                  </div>

                  <div className="my-1 h-px bg-border" />

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

            {/* Knowledge */}
            <SectionHeading icon={Database} label="Knowledge" open={sections.knowledge} onToggle={() => toggleSection("knowledge")} />
            {sections.knowledge && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <ToggleRow
                    label="Auto-inject KB Context"
                    enabled={settings.enable_auto_inject}
                    onToggle={(v) => patch({ enable_auto_inject: v })}
                    info="Automatically includes relevant KB context when relevance exceeds threshold"
                  />
                  {settings.enable_auto_inject && (
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <LabelWithInfo
                          label={`Injection Threshold: ${settings.auto_inject_threshold.toFixed(2)}`}
                          info="Minimum relevance score to auto-inject (higher = more selective)"
                        />
                      </div>
                      <input
                        type="range"
                        min={0.5}
                        max={1}
                        step={0.05}
                        value={settings.auto_inject_threshold}
                        onChange={(e) =>
                          patch({ auto_inject_threshold: parseFloat(e.target.value) })
                        }
                        className="h-1.5 w-32 cursor-pointer accent-primary"
                        aria-label="Auto-inject threshold"
                      />
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Taxonomy */}
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

            {/* Encryption */}
            <SectionHeading icon={Shield} label="Encryption" open={sections.encryption} onToggle={() => toggleSection("encryption")} />
            {sections.encryption && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Status" info="Whether data at rest is encrypted" />
                    <Badge variant={settings.enable_encryption ? "default" : "secondary"}>
                      {settings.enable_encryption ? "Enabled" : "Disabled"}
                    </Badge>
                  </div>
                  <Row label="Sync Backend" value={settings.sync_backend} info="Storage backend for cross-device sync" />
                </CardContent>
              </Card>
            )}

            {/* Infrastructure */}
            <SectionHeading icon={Wrench} label="Infrastructure" open={sections.infrastructure} onToggle={() => toggleSection("infrastructure")} />
            {sections.infrastructure && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <Row label="Bifrost URL" value={settings.bifrost_url ?? "—"} mono info="LLM gateway endpoint" />
                  <Row label="Bifrost Timeout" value={settings.bifrost_timeout ? `${settings.bifrost_timeout}s` : "—"} info="Request timeout for LLM calls" />
                  <Row label="ChromaDB" value={settings.chroma_url ?? "—"} mono info="Vector database endpoint" />
                  <Row label="Neo4j" value={settings.neo4j_uri ?? "—"} mono info="Graph database endpoint" />
                  <Row label="Redis" value={settings.redis_url ?? "—"} mono info="Cache and BM25 index (password redacted)" />
                  <Row label="Archive Path" value={settings.archive_path ?? "—"} mono info="File archive mount path" />
                  <Row label="Chunking Mode" value={settings.chunking_mode ?? "—"} info="Token-based or semantic chunking" />
                </CardContent>
              </Card>
            )}

            {/* Search Tuning */}
            <SectionHeading icon={SearchIcon} label="Search Tuning" open={sections.search} onToggle={() => toggleSection("search")} />
            {sections.search && (
              <Card className="mb-4">
                <CardContent className="grid gap-4 pt-4">
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <LabelWithInfo
                        label={`Vector Weight: ${(settings.hybrid_vector_weight ?? 0.6).toFixed(2)}`}
                        info="Weight for vector similarity in hybrid search (0–1)"
                      />
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={settings.hybrid_vector_weight ?? 0.6}
                      onChange={(e) => patch({ hybrid_vector_weight: parseFloat(e.target.value) })}
                      className="h-1.5 w-32 cursor-pointer accent-primary"
                      aria-label="Hybrid vector weight"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <LabelWithInfo
                        label={`Keyword Weight: ${(settings.hybrid_keyword_weight ?? 0.4).toFixed(2)}`}
                        info="Weight for BM25 keyword matching in hybrid search (0–1)"
                      />
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={settings.hybrid_keyword_weight ?? 0.4}
                      onChange={(e) => patch({ hybrid_keyword_weight: parseFloat(e.target.value) })}
                      className="h-1.5 w-32 cursor-pointer accent-primary"
                      aria-label="Hybrid keyword weight"
                    />
                  </div>

                  <div className="my-1 h-px bg-border" />

                  <div className="flex items-center justify-between gap-4">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <LabelWithInfo
                        label={`Rerank LLM Weight: ${(settings.rerank_llm_weight ?? 0.6).toFixed(2)}`}
                        info="Weight for LLM-based reranking score (0–1)"
                      />
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={settings.rerank_llm_weight ?? 0.6}
                      onChange={(e) => patch({ rerank_llm_weight: parseFloat(e.target.value) })}
                      className="h-1.5 w-32 cursor-pointer accent-primary"
                      aria-label="Rerank LLM weight"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <LabelWithInfo
                        label={`Rerank Original Weight: ${(settings.rerank_original_weight ?? 0.4).toFixed(2)}`}
                        info="Weight for original relevance score in reranking (0–1)"
                      />
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={settings.rerank_original_weight ?? 0.4}
                      onChange={(e) => patch({ rerank_original_weight: parseFloat(e.target.value) })}
                      className="h-1.5 w-32 cursor-pointer accent-primary"
                      aria-label="Rerank original weight"
                    />
                  </div>

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

            {/* Sync */}
            <SectionHeading icon={FolderSync} label="Sync" open={sections.sync} onToggle={() => toggleSection("sync")} />
            {sections.sync && <SyncSection />}

            {/* KB Management */}
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

            {/* Feature Flags */}
            <SectionHeading icon={Layers} label="Feature Flags" open={sections.flags} onToggle={() => toggleSection("flags")} />
            {sections.flags && (
              <Card className="mb-4">
                <CardContent className="grid gap-2 pt-4">
                  {Object.entries(settings.feature_flags).map(([flag, enabled]) => (
                    <div key={flag} className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">{flag}</span>
                      <Badge variant={enabled ? "default" : "outline"} className="text-[10px]">
                        {enabled ? "on" : "off"}
                      </Badge>
                    </div>
                  ))}
                  {Object.keys(settings.feature_flags).length === 0 && (
                    <p className="text-xs text-muted-foreground">No feature flags configured</p>
                  )}
                </CardContent>
              </Card>
            )}
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
      <p className="text-xs text-muted-foreground">Server configuration and feature toggles</p>
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
    <div className="flex items-center justify-between">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <Button
        variant="ghost"
        size="sm"
        className={cn("h-7 px-2 text-xs", enabled ? "text-green-500" : "text-muted-foreground")}
        onClick={() => onToggle(!enabled)}
      >
        {enabled ? "On" : "Off"}
      </Button>
    </div>
  )
}
