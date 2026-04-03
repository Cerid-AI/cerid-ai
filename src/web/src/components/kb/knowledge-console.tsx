// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
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
} from "lucide-react"
import { cn } from "@/lib/utils"
import { fetchDataSources, enableDataSource, disableDataSource, updateSettings } from "@/lib/api"
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
  children,
}: {
  title: string
  icon: React.ReactNode
  count: number
  enabled: boolean
  onToggle: () => void
  color: string
  children: React.ReactNode
}) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className="border-b last:border-b-0">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
        {icon}
        <span className="flex-1 text-xs font-medium">{title}</span>
        <Badge variant="secondary" className={cn("text-[10px] px-1.5 py-0", color)}>
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
          <p className="truncate text-[11px] font-medium">{result.filename}</p>
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
            {Math.round(result.relevance * 100)}%
          </span>
        </div>
        <p className="text-[10px] text-muted-foreground">{result.domain}</p>
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
          <p className="truncate text-[11px] font-medium">{result.summary || result.content.slice(0, 60)}</p>
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
            {Math.round(result.relevance * 100)}%
          </span>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          <Badge variant="outline" className="text-[9px] px-1 py-0">{result.memory_type}</Badge>
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
          <p className="truncate text-[11px] font-medium">{result.source_name ?? "External"}</p>
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
            {Math.round(result.relevance * 100)}%
          </span>
        </div>
        <p className="mt-0.5 text-[10px] text-muted-foreground line-clamp-2">{result.content}</p>
        {result.source_url && (
          <a href={result.source_url} target="_blank" rel="noopener noreferrer" className="mt-0.5 inline-block text-[10px] text-primary hover:underline">
            Source &rarr;
          </a>
        )}
      </div>
    </div>
  )
}

/** Compact data source status list — shows enabled APIs with inline toggles. */
function DataSourceIndicator() {
  const { data, refetch } = useQuery({
    queryKey: ["data-sources"],
    queryFn: fetchDataSources,
    staleTime: 60_000,
  })
  const [toggling, setToggling] = useState<string | null>(null)

  if (!data?.sources?.length) return null

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

  const enabledCount = data.sources.filter((s) => s.enabled && s.configured).length

  return (
    <div className="mt-1.5 space-y-1">
      <p className="text-[10px] text-muted-foreground font-medium">
        APIs ({enabledCount}/{data.sources.length} active)
      </p>
      {data.sources.map((src) => (
        <div key={src.name} className="flex items-center justify-between rounded-md bg-muted/30 px-2 py-1">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", src.enabled && src.configured ? "bg-green-500" : "bg-muted-foreground/40")} />
            <span className="text-[10px] truncate">{src.name}</span>
            {src.requires_api_key && !src.configured && (
              <Badge variant="outline" className="text-[8px] px-1 py-0 text-amber-600 dark:text-yellow-400 border-yellow-500/30">key needed</Badge>
            )}
          </div>
          <Switch
            checked={src.enabled}
            onCheckedChange={() => handleToggle(src.name, src.enabled)}
            disabled={toggling === src.name || (src.requires_api_key && !src.configured)}
            className="scale-[0.6]"
          />
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
  try { const v = localStorage.getItem(key); if (v !== null) { const n = parseFloat(v); if (!isNaN(n)) return n } } catch { /* noop */ }
  return fallback
}

function ConsoleConfigBar({
  ragMode,
  onRagModeChange,
}: {
  ragMode: RagMode
  onRagModeChange: (mode: RagMode) => void
}) {
  const [reranking, setReranking] = useState(() => readLocalBool("cerid-console-reranking", true))
  const [graphRag, setGraphRag] = useState(() => readLocalBool("cerid-console-graph-rag", false))
  const [queryDecomp, setQueryDecomp] = useState(() => readLocalBool("cerid-console-query-decomp", false))
  const [topK, setTopK] = useState(() => readLocalNumber("cerid-console-top-k", 10))

  const persistBool = useCallback((key: string, value: boolean, serverKey?: string) => {
    try { localStorage.setItem(key, String(value)) } catch { /* noop */ }
    if (serverKey) updateSettings({ [serverKey]: value } as Record<string, boolean>).catch(() => { /* noop */ })
  }, [])

  const handleReranking = useCallback((v: boolean) => {
    setReranking(v)
    persistBool("cerid-console-reranking", v)
  }, [persistBool])

  const handleGraphRag = useCallback((v: boolean) => {
    setGraphRag(v)
    persistBool("cerid-console-graph-rag", v)
  }, [persistBool])

  const handleQueryDecomp = useCallback((v: boolean) => {
    setQueryDecomp(v)
    persistBool("cerid-console-query-decomp", v, "enable_query_decomposition")
  }, [persistBool])

  const handleTopK = useCallback((v: number[]) => {
    const val = v[0]
    setTopK(val)
    try { localStorage.setItem("cerid-console-top-k", String(val)) } catch { /* noop */ }
  }, [])

  const RAG_MODES: { value: RagMode; label: string }[] = [
    { value: "manual", label: "Manual" },
    { value: "smart", label: "Smart" },
    { value: "custom_smart", label: "Custom" },
  ]

  return (
    <div className="flex items-center gap-1.5 border-b bg-muted/20 px-3 py-1.5">
      {/* RAG Mode selector */}
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm" className="h-6 gap-1 px-2 text-[10px]">
            {ragMode === "smart" ? "Smart" : ragMode === "custom_smart" ? "Custom" : "Manual"}
            <ChevronDown className="h-3 w-3" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-36 p-1" align="start">
          {RAG_MODES.map((m) => (
            <button
              key={m.value}
              className={cn(
                "flex w-full items-center rounded-sm px-2 py-1 text-xs hover:bg-accent",
                ragMode === m.value && "bg-accent font-medium",
              )}
              onClick={() => onRagModeChange(m.value)}
            >
              {m.label}
            </button>
          ))}
        </PopoverContent>
      </Popover>

      <div className="flex-1" />

      {/* Pipeline settings gear */}
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="icon" className="h-6 w-6">
            <Settings2 className="h-3.5 w-3.5" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-56 space-y-3 p-3" align="end">
          <p className="text-xs font-medium">Pipeline Settings</p>

          <div className="flex items-center justify-between">
            <Label className="text-[11px]">Reranking</Label>
            <Switch checked={reranking} onCheckedChange={handleReranking} className="scale-75" />
          </div>

          <div className="flex items-center justify-between">
            <Label className="text-[11px]">Graph RAG</Label>
            <Switch checked={graphRag} onCheckedChange={handleGraphRag} className="scale-75" />
          </div>

          <div className="flex items-center justify-between">
            <Label className="text-[11px]">Query Decomposition</Label>
            <Switch checked={queryDecomp} onCheckedChange={handleQueryDecomp} className="scale-75" />
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-[11px]">Top-K</Label>
              <span className="text-[10px] tabular-nums text-muted-foreground">{topK}</span>
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
  onClose,
}: KnowledgeConsoleProps) {
  const confidencePct = Math.round(confidence * 100)
  const totalSources = kbSources.length + memorySources.length + externalSources.length

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="flex-1 text-sm font-medium">Knowledge Console</span>
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-teal-500">
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
                  <p className="text-[11px] text-muted-foreground py-1">No KB matches</p>
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
                  <p className="text-[11px] text-muted-foreground py-1">No memory matches</p>
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
              >
                {externalSources.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground py-1">No external results</p>
                ) : (
                  externalSources.map((r, i) => <ExternalSourceCard key={`ext-${i}`} result={r} />)
                )}
                <DataSourceIndicator />
              </SourceSection>
            </>
          )}
        </ScrollArea>
      )}

      {/* Footer — confidence + source summary */}
      {hasQueried && totalSources > 0 && (
        <div className="border-t bg-muted/30 px-3 py-2">
          {/* Confidence bar */}
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[11px] text-muted-foreground">Confidence</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${confidencePct}%` }}
              />
            </div>
            <span className="text-[11px] font-medium tabular-nums">{confidencePct}%</span>
          </div>
          <div className="flex items-center justify-between text-[10px] text-muted-foreground">
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
