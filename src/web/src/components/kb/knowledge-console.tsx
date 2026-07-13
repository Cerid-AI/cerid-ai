// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { logSwallowedError } from "@/lib/log-swallowed"
import { safeHttpUrl } from "@/lib/kb-utils"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Label } from "@/components/ui/label"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { Badge } from "@/components/ui/badge"
import {
  X,
  Loader2,
  ChevronDown,
  ChevronRight,
  Database,
  Brain,
  Globe,
  FileText,
  ToggleLeft,
  ToggleRight,
  AlertCircle,
  Settings2,
  Search,
} from "lucide-react"
import { Plus } from "lucide-react"
import { Input } from "@/components/ui/input"
import { DomainFilter } from "./domain-filter"
import { ProgressBar } from "@/components/ui/progress-bar"
import { cn } from "@/lib/utils"
import { fetchDataSources, updateSettings } from "@/lib/api"
import { useNavigation } from "@/contexts/navigation-context"
import { CustomApiDialog } from "./custom-api-dialog"
import { IngestionProgress } from "./ingestion-progress"
import type { UseOrchestratedQueryReturn } from "@/hooks/use-orchestrated-query"
import type { KBQueryResult, MemoryRecallResult, ExternalSourceResult, RagMode } from "@/lib/types"

interface KnowledgeConsoleProps extends UseOrchestratedQueryReturn {
  ragMode: RagMode
  onRagModeChange?: (mode: RagMode) => void
  onClose: () => void
}

function SourceSection({
  title,
  icon,
  count,
  enabled,
  onToggle,
  color,
  defaultExpanded = true,
  children,
}: {
  title: string
  icon: React.ReactNode
  count: number
  enabled: boolean
  onToggle: () => void
  color: string
  defaultExpanded?: boolean
  children: React.ReactNode
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <div className="border-b last:border-b-0">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
        {icon}
        <span className="flex-1 text-xs font-medium">{title}</span>
        <Badge variant="secondary" className={cn("text-label-xs px-1.5 py-0", color)}>
          {count}
        </Badge>
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                className="ml-1 shrink-0"
                onClick={(e) => {
                  e.stopPropagation()
                  onToggle()
                }}
                aria-label={enabled ? `Disable ${title}` : `Enable ${title}`}
              >
                {enabled ? (
                  <ToggleRight className="h-4 w-4 text-teal-500" />
                ) : (
                  <ToggleLeft className="h-4 w-4 text-muted-foreground" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent>{enabled ? `${title}: included` : `${title}: excluded`}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </button>
      {expanded && enabled && (
        <div className="space-y-1 px-3 pb-2">
          {children}
        </div>
      )}
    </div>
  )
}

function KBSourceCard({ result }: { result: KBQueryResult }) {
  return (
    <div className="flex items-start gap-2 rounded-md border px-2.5 py-1.5">
      <FileText className="h-3 w-3 shrink-0 mt-0.5 text-blue-400" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1">
          <p className="truncate text-label-sm font-medium">{result.filename}</p>
          <span className="shrink-0 text-label-xs tabular-nums text-muted-foreground">
            {Math.round(result.relevance * 100)}%
          </span>
        </div>
        <p className="text-label-xs text-muted-foreground">{result.domain}</p>
      </div>
    </div>
  )
}

function MemorySourceCard({ result }: { result: MemoryRecallResult }) {
  const typeColors: Record<string, string> = {
    empirical: "text-blue-400",
    decision: "text-amber-400",
    preference: "text-green-400",
    project_context: "text-purple-400",
    temporal: "text-orange-400",
    conversational: "text-cyan-400",
  }

  return (
    <div className="flex items-start gap-2 rounded-md border px-2.5 py-1.5">
      <Brain className={cn("h-3 w-3 shrink-0 mt-0.5", typeColors[result.memory_type] ?? "text-muted-foreground")} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1">
          <p className="truncate text-label-sm font-medium">{result.summary || result.content.slice(0, 60)}</p>
          <span className="shrink-0 text-label-xs tabular-nums text-muted-foreground">
            {Math.round(result.relevance * 100)}%
          </span>
        </div>
        <div className="flex items-center gap-2 text-label-xs text-muted-foreground">
          <Badge variant="outline" className="text-label-xxs px-1 py-0">{result.memory_type}</Badge>
          <span>{Math.round(result.age_days)}d ago</span>
        </div>
      </div>
    </div>
  )
}

function ExternalSourceCard({ result }: { result: ExternalSourceResult }) {
  return (
    <div className="flex items-start gap-2 rounded-md border px-2.5 py-1.5">
      <Globe className="h-3 w-3 shrink-0 mt-0.5 text-green-400" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1">
          <p className="truncate text-label-sm font-medium">{result.source_name ?? "External"}</p>
          <span className="shrink-0 text-label-xs tabular-nums text-muted-foreground">
            {Math.round(result.relevance * 100)}%
          </span>
        </div>
        <p className="mt-0.5 text-label-xs text-muted-foreground line-clamp-2">{result.content}</p>
        {safeHttpUrl(result.source_url) && (
          <a href={safeHttpUrl(result.source_url)!} target="_blank" rel="noopener noreferrer" className="mt-0.5 inline-block text-label-xs text-primary hover:underline">
            Source &rarr;
          </a>
        )}
      </div>
    </div>
  )
}

/** Compact data source status list — read-only. Enable/disable moved to the
    unified Settings → Extensions → Knowledge Providers section (P0-C.4) so
    each service is toggled in one place with its scope labelled. */
function DataSourceIndicator() {
  const { goTo } = useNavigation()
  const { data } = useQuery({
    queryKey: ["data-sources"],
    queryFn: () => fetchDataSources(),
    staleTime: 60_000,
  })

  if (!data?.sources?.length) return null

  const enabledCount = data.sources.filter((s) => s.enabled && s.configured).length

  return (
    <div className="mt-1.5 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <p className="text-label-xs text-muted-foreground font-medium">
          APIs ({enabledCount}/{data.sources.length} active)
        </p>
        <button
          type="button"
          onClick={() => goTo("settings", { category: "extensions", setting: "extensions.externalApis.enable" })}
          className="text-label-xs text-primary hover:underline shrink-0"
          aria-label="Manage knowledge providers in Settings"
        >
          Manage
        </button>
      </div>
      {data.sources.map((src) => (
        <div key={src.name} className="flex items-center justify-between rounded-md bg-muted/30 px-2 py-1">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", src.enabled && src.configured ? "bg-green-500" : "bg-muted-foreground/40")} />
            <span className="text-label-xs truncate">{src.name}</span>
            {src.requires_api_key && !src.configured && (
              <Badge variant="outline" className="text-label-xxs px-1 py-0 text-amber-600 dark:text-yellow-400 border-yellow-500/30">key needed</Badge>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ---- Compact pipeline config bar for inline Knowledge Console controls ---- */

function readLocalBool(key: string, fallback: boolean): boolean {
  try { const v = localStorage.getItem(key); return v === null ? fallback : v === "true" } catch { return fallback }
}

function readLocalNumber(key: string, fallback: number): number {
  try { const v = localStorage.getItem(key); if (v !== null) { const n = parseFloat(v); if (!isNaN(n)) return n } } catch (err) { logSwallowedError(err, "localStorage.getItem", { key }) }
  return fallback
}

function ConsoleConfigBar({
  ragMode,
}: {
  ragMode: RagMode
  onRagModeChange: (mode: RagMode) => void
}) {
  const [selfRag, setSelfRag] = useState(() => readLocalBool("cerid-console-self-rag", false))
  const [queryDecomp, setQueryDecomp] = useState(() => readLocalBool("cerid-console-query-decomp", false))
  const [semanticCache, setSemanticCache] = useState(() => readLocalBool("cerid-console-semantic-cache", false))
  const [topK, setTopK] = useState(() => readLocalNumber("cerid-console-top-k", 10))

  const persistBool = useCallback((key: string, value: boolean, serverKey?: string) => {
    try { localStorage.setItem(key, String(value)) } catch (err) { logSwallowedError(err, "localStorage.setItem", { key }) }
    if (serverKey) updateSettings({ [serverKey]: value } as Record<string, boolean>).catch(() => { /* noop */ })
  }, [])

  const handleSelfRag = useCallback((v: boolean) => {
    setSelfRag(v)
    persistBool("cerid-console-self-rag", v, "enable_self_rag")
  }, [persistBool])

  const handleQueryDecomp = useCallback((v: boolean) => {
    setQueryDecomp(v)
    persistBool("cerid-console-query-decomp", v, "enable_query_decomposition")
  }, [persistBool])

  const handleSemanticCache = useCallback((v: boolean) => {
    setSemanticCache(v)
    persistBool("cerid-console-semantic-cache", v, "enable_semantic_cache")
  }, [persistBool])

  const handleTopK = useCallback((v: number[]) => {
    const val = v[0]
    setTopK(val)
    try { localStorage.setItem("cerid-console-top-k", String(val)) } catch (err) { logSwallowedError(err, "localStorage.setItem", { key: "cerid-console-top-k" }) }
  }, [])

  return (
    <div className="sticky top-0 z-10 flex items-center gap-1.5 border-b bg-background/95 backdrop-blur px-3 py-1.5">
      {/* RAG Mode display (read-only — primary control is in chat toolbar) */}
      <span className="rounded bg-muted px-2 py-0.5 text-label-xs font-medium text-muted-foreground">
        {ragMode === "smart" ? "Smart" : ragMode === "always" ? "Always" : "Off"}
      </span>

      <div className="flex-1" />

      {/* Pipeline settings gear */}
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="icon" className="h-6 w-6">
            <Settings2 className="h-3.5 w-3.5" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-64 space-y-3 p-3" align="end">
          <p className="text-xs font-medium">Pipeline Settings</p>

          {/* Self-RAG toggle */}
          <div className="flex items-center justify-between">
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Label className="text-label-sm cursor-help border-b border-dotted border-muted-foreground/40">Self-RAG</Label>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-[200px] text-xs"> {/* drift-allowed: TooltipContent max-width cap keeps pipeline-setting help text from sprawling */}
                  Validates response claims against KB. Adds ~1s latency.
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <Switch checked={selfRag} onCheckedChange={handleSelfRag} className="scale-75" />
          </div>

          {/* Query Decomposition toggle */}
          <div className="flex items-center justify-between">
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Label className="text-label-sm cursor-help border-b border-dotted border-muted-foreground/40">Query Decomposition</Label>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-[200px] text-xs"> {/* drift-allowed: TooltipContent max-width cap keeps pipeline-setting help text from sprawling */}
                  Splits complex queries into sub-queries for better coverage.
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <Switch checked={queryDecomp} onCheckedChange={handleQueryDecomp} className="scale-75" />
          </div>

          {/* Semantic Cache toggle */}
          <div className="flex items-center justify-between">
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Label className="text-label-sm cursor-help border-b border-dotted border-muted-foreground/40">Semantic Cache</Label>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-[200px] text-xs"> {/* drift-allowed: TooltipContent max-width cap keeps pipeline-setting help text from sprawling */}
                  Caches similar queries to skip repeated retrieval. Saves ~500ms on cache hits.
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <Switch checked={semanticCache} onCheckedChange={handleSemanticCache} className="scale-75" />
          </div>

          {/* NLI — read-only indicator */}
          <div className="flex items-center justify-between">
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Label className="text-label-sm cursor-help border-b border-dotted border-muted-foreground/40">NLI Verification</Label>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-[220px] text-xs"> {/* drift-allowed: TooltipContent max-width cap keeps pipeline-setting help text from sprawling */}
                  NLI entailment model validates KB evidence. Threshold: 0.7 entailment / 0.6 contradiction.
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <Badge variant="secondary" className="text-label-xxs px-1.5 py-0">Active</Badge>
          </div>

          {/* Top-K slider */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-label-sm">Top-K</Label>
              <span className="text-label-xs tabular-nums text-muted-foreground">{topK}</span>
            </div>
            <Slider value={[topK]} onValueChange={handleTopK} min={3} max={20} step={1} className="w-full" />
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}

export function KnowledgeConsole({
  ragMode,
  onRagModeChange,
  confidence,
  isLoading,
  isError,
  refetch,
  hasQueried,
  kbSources,
  memorySources,
  externalSources,
  kbEnabled,
  memoryEnabled,
  externalEnabled,
  toggleKB,
  toggleMemory,
  toggleExternal,
  executionTime,
  activeDomains,
  toggleDomain,
  manualQuery,
  setManualQuery,
  executeManualSearch,
  clearManualSearch,
  onClose,
}: KnowledgeConsoleProps) {
  const confidencePct = Math.round(confidence * 100)
  const totalSources = kbSources.length + memorySources.length + externalSources.length
  const [customApiOpen, setCustomApiOpen] = useState(false)

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="flex-1 text-sm font-medium">Knowledge Console</span>
        <Badge variant="outline" className="text-label-xs px-1.5 py-0 text-teal-500">
          {ragMode === "smart" ? "Smart" : "Custom"}
        </Badge>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose} aria-label="Close">
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Configuration bar — RAG mode + pipeline settings */}
      {onRagModeChange && (
        <ConsoleConfigBar ragMode={ragMode} onRagModeChange={onRagModeChange} />
      )}

      {/* Live ingestion progress — appears when files are being ingested */}
      <IngestionProgress />

      {/* CH7: domain filter + manual search (state already plumbed via props) */}
      <div className="space-y-2 border-b px-3 py-2">
        <div role="group" aria-label="Domain filter">
          <DomainFilter activeDomains={activeDomains} onToggle={toggleDomain} />
        </div>
        <div className="flex items-center gap-1.5">
          <Input
            value={manualQuery}
            onChange={(e) => setManualQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") executeManualSearch()
              if (e.key === "Escape") clearManualSearch()
            }}
            placeholder="Search knowledge…"
            aria-label="Manual knowledge search"
            className="h-8"
          />
          <Button size="sm" variant="ghost" onClick={executeManualSearch} aria-label="Run search">
            <Search className="h-4 w-4" />
          </Button>
          {manualQuery && (
            <Button size="sm" variant="ghost" onClick={clearManualSearch} aria-label="Clear search">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Loading / error state */}
      {isLoading && (
        <div className="flex items-center gap-2 px-3 py-4">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          <span className="text-xs">Searching KB + memory + external...</span>
        </div>
      )}
      {isError && (
        <div className="flex flex-col items-center gap-2 py-8 text-muted-foreground">
          <AlertCircle className="h-4 w-4 text-destructive" />
          <span className="text-sm">Query failed</span>
          <button onClick={() => refetch()} className="text-xs text-primary hover:underline">Retry</button>
        </div>
      )}

      {/* Source sections */}
      {!isLoading && !isError && (
        <ScrollArea className="min-h-0 flex-1">
          {!hasQueried && (
            <div className="py-8 text-center text-sm text-muted-foreground">
              Send a message to see knowledge sources
            </div>
          )}

          {hasQueried && (
            <>
              <SourceSection
                title="KB Sources"
                icon={<Database className="h-3 w-3 shrink-0 text-blue-400" />}
                count={kbSources.length}
                enabled={kbEnabled}
                onToggle={toggleKB}
                color="bg-blue-500/10 text-blue-500"
              >
                {kbSources.length === 0 ? (
                  <p className="text-label-sm text-muted-foreground py-1">No KB matches</p>
                ) : (
                  kbSources.map((r, i) => <KBSourceCard key={`kb-${r.artifact_id}-${r.chunk_index}-${i}`} result={r} />)
                )}
              </SourceSection>

              <SourceSection
                title="Memories"
                icon={<Brain className="h-3 w-3 shrink-0 text-purple-400" />}
                count={memorySources.length}
                enabled={memoryEnabled}
                onToggle={toggleMemory}
                color="bg-purple-500/10 text-purple-500"
              >
                {memorySources.length === 0 ? (
                  <p className="text-label-sm text-muted-foreground py-1">No memory matches</p>
                ) : (
                  memorySources.map((r, i) => <MemorySourceCard key={`mem-${r.memory_id}-${i}`} result={r} />)
                )}
              </SourceSection>

              <SourceSection
                title="External"
                icon={<Globe className="h-3 w-3 shrink-0 text-green-400" />}
                count={externalSources.length}
                enabled={externalEnabled}
                onToggle={toggleExternal}
                color="bg-green-500/10 text-green-500"
                defaultExpanded={false}
              >
                {externalSources.length === 0 ? (
                  <p className="text-label-sm text-muted-foreground py-1">No external results</p>
                ) : (
                  externalSources.map((r, i) => <ExternalSourceCard key={`ext-${i}`} result={r} />)
                )}
                <DataSourceIndicator />
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-1 h-6 gap-1 text-label-xs w-full"
                  onClick={() => setCustomApiOpen(true)}
                >
                  <Plus className="h-3 w-3" /> Add Custom API
                </Button>
                <CustomApiDialog
                  open={customApiOpen}
                  onClose={() => setCustomApiOpen(false)}
                  onSave={async () => { setCustomApiOpen(false) }}
                />
              </SourceSection>
            </>
          )}
        </ScrollArea>
      )}

      {/* Footer — relevance + source summary */}
      {hasQueried && totalSources > 0 && (
        <div className="border-t bg-muted/30 px-3 py-2">
          {/* Relevance bar */}
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-label-sm text-muted-foreground">Relevance</span>
            <ProgressBar pct={confidencePct} className="flex-1" />
            <span className="text-label-sm font-medium tabular-nums">{confidencePct}%</span>
          </div>
          <div className="flex items-center justify-between text-label-xs text-muted-foreground">
            <span>
              {kbSources.length} KB &middot; {memorySources.length} memory &middot; {externalSources.length} external
            </span>
            <span>{executionTime}ms</span>
          </div>
        </div>
      )}
    </div>
  )
}
