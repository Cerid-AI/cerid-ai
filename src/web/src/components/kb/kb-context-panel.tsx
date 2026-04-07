// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useMemo } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { X, Search, Loader2, Zap, Upload, Database, FileText, Layers, Archive, FileInput, AlertCircle, Globe, Brain, ChevronDown, ChevronRight, Plus } from "lucide-react"
import { ArtifactCard } from "./artifact-card"
import { DomainFilter } from "./domain-filter"
import { TagFilter } from "./tag-filter"
import { GraphPreview } from "./graph-preview"
import { UploadDialog } from "./upload-dialog"
import { useSettings } from "@/hooks/use-settings"
import { useDragDrop } from "@/hooks/use-drag-drop"
import type { UseKBContextReturn } from "@/hooks/use-kb-context"
import { uploadFile, fetchKBStats, recallMemories } from "@/lib/api"
import type { KBStats } from "@/lib/api"
import type { MemoryRecallResult, KBQueryResult } from "@/lib/types"
import { cn } from "@/lib/utils"

interface KBContextPanelProps extends UseKBContextReturn {
  onClose: () => void
}

export function KBContextPanel({
  results,
  confidence,
  totalResults: _totalResults,
  isLoading,
  error,
  isError,
  refetch,
  hasQueried,
  activeDomains,
  toggleDomain,
  activeTags,
  toggleTag,
  manualQuery,
  setManualQuery,
  executeManualSearch,
  clearManualSearch,
  selectedArtifactId,
  setSelectedArtifactId,
  injectedContext,
  injectResult,
  onClose,
}: KBContextPanelProps) {
  const confidencePct = Math.round(confidence * 100)
  const { autoInject, toggleAutoInject } = useSettings()

  // Memory recall state
  const [memoryResults, setMemoryResults] = useState<MemoryRecallResult[]>([])
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [memorySectionOpen, setMemorySectionOpen] = useState(true)
  const [externalSectionOpen, setExternalSectionOpen] = useState(true)

  // Auto-recall memories when results change (piggyback on KB query)
  const latestQuery = manualQuery || ""
  useEffect(() => {
    if (!hasQueried || !results.length) return
    queueMicrotask(() => {
      setMemoryLoading(true)
      // Use the query that produced the current KB results
      recallMemories(latestQuery || results[0]?.content?.slice(0, 100) || "", 5, 0.4)
        .then(setMemoryResults)
        .catch(() => setMemoryResults([]))
        .finally(() => setMemoryLoading(false))
    })
  }, [hasQueried, results.length, latestQuery]) // eslint-disable-line react-hooks/exhaustive-deps

  // Separate external sources from KB results
  const { kbResults, externalResults } = useMemo(() => {
    const kb: KBQueryResult[] = []
    const ext: KBQueryResult[] = []
    for (const r of results) {
      if (r.domain === "external" || r.source_url) {
        ext.push(r)
      } else {
        kb.push(r)
      }
    }
    return { kbResults: kb, externalResults: ext }
  }, [results])

  // Drag-drop for file ingestion
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const { isDragOver, dragHandlers } = useDragDrop(setPendingFiles)

  // KB stats
  const [kbStats, setKbStats] = useState<KBStats | null>(null)
  const [archiveMode, setArchiveMode] = useState(false)
  useEffect(() => {
    fetchKBStats().then(setKbStats).catch(() => {})
  }, [])

  const handleUploadConfirm = useCallback(
    async (options: { domain?: string; categorize_mode?: string }) => {
      const files = [...pendingFiles]
      setPendingFiles([])
      for (const file of files) {
        try {
          await uploadFile(file, { domain: options.domain, categorizeMode: options.categorize_mode })
        } catch { /* upload errors handled by API toast */ }
      }
    },
    [pendingFiles],
  )

  return (
    <div
      className="relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden"
      {...dragHandlers}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-primary/10">
          <div className="flex flex-col items-center gap-2 text-primary">
            <Upload className="h-8 w-8" />
            <span className="text-sm font-medium">Drop to ingest</span>
          </div>
        </div>
      )}

      {/* Upload dialog */}
      <UploadDialog
        files={pendingFiles}
        defaultDomain={activeDomains.size === 1 ? [...activeDomains][0] : null}
        onConfirm={handleUploadConfirm}
        onCancel={() => setPendingFiles([])}
      />

      {/* Header */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="flex-1 text-sm font-medium">Knowledge Context</span>
        {results.length > 0 && (
          <span className="text-xs text-muted-foreground">{results.length} results</span>
        )}
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn("h-7 w-7", autoInject && "text-green-500")}
                onClick={toggleAutoInject}
                aria-label={autoInject ? "Disable auto-inject" : "Enable auto-inject"}
              >
                <Zap className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{autoInject ? "Auto-inject: ON" : "Auto-inject: OFF"}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose} aria-label="Close knowledge panel">
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Search */}
      <div className="border-b px-3 py-2">
        <div className="flex min-w-0 gap-1.5">
          <Input
            placeholder="Search knowledge base..."
            aria-label="Search knowledge base"
            value={manualQuery}
            onChange={(e) => setManualQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") executeManualSearch()
              if (e.key === "Escape") clearManualSearch()
            }}
            className="h-8 text-xs"
          />
          <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={executeManualSearch} aria-label="Search knowledge base">
            <Search className="h-4 w-4" />
          </Button>
        </div>
        <div className="mt-2">
          <DomainFilter activeDomains={activeDomains} onToggle={toggleDomain} />
        </div>
        <div className="mt-2">
          <TagFilter
            activeTags={activeTags}
            onToggleTag={toggleTag}
            domain={activeDomains.size === 1 ? [...activeDomains][0] : null}
          />
        </div>
      </div>

      {/* Relevance bar — only show when there are visible results */}
      {results.length > 0 && (
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex items-center gap-2 px-3 py-1.5">
                <span className="text-xs text-muted-foreground">Relevance</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all"
                    style={{ width: `${confidencePct}%` }}
                  />
                </div>
                <span className="text-xs font-medium tabular-nums">{confidencePct}%</span>
              </div>
            </TooltipTrigger>
            <TooltipContent>Relevance: {confidencePct}% match to your query</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}

      {/* Results */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="min-w-0 space-y-2 p-3">
          {isLoading && (
            <div className="space-y-1 px-3 py-4">
              <div className="flex items-center gap-2">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                <span className="text-xs font-medium">Searching knowledge base...</span>
              </div>
              <div className="space-y-0.5 font-mono text-[10px] text-muted-foreground/70">
                {kbStats ? (
                  <p>
                    {"\u2192"} Querying {activeDomains.size > 0 ? activeDomains.size : Object.keys(kbStats.domains).length}{" "}
                    domain{(activeDomains.size > 0 ? activeDomains.size : Object.keys(kbStats.domains).length) !== 1 ? "s" : ""}
                    {activeDomains.size > 0 && (
                      <span className="text-muted-foreground/50"> ({[...activeDomains].join(", ")})</span>
                    )}
                  </p>
                ) : (
                  <p>{"\u2192"} Querying all domains</p>
                )}
                <p>{"\u2192"} Hybrid search (BM25 + vector)</p>
              </div>
            </div>
          )}

          {isError && (
            <div className="flex flex-col items-center gap-2 py-8 text-muted-foreground">
              <AlertCircle className="h-4 w-4 text-destructive" />
              <span className="text-sm">Knowledge query failed</span>
              <button onClick={() => refetch()} className="text-xs text-primary hover:underline">Retry</button>
            </div>
          )}

          {!isLoading && !error && results.length === 0 && (
            <div className="py-8 text-center text-sm text-muted-foreground">
              {hasQueried ? "No matching knowledge found" : "Send a message to see related knowledge"}
            </div>
          )}

          {/* KB Results */}
          {kbResults.map((result) => (
            <ArtifactCard
              key={`${result.artifact_id}-${result.chunk_index}`}
              result={result}
              isSelected={selectedArtifactId === result.artifact_id}
              onSelect={() =>
                setSelectedArtifactId(
                  selectedArtifactId === result.artifact_id ? null : result.artifact_id,
                )
              }
              onInject={() => injectResult(result)}
            />
          ))}

          {/* Memories section */}
          {hasQueried && (
            <div className="mt-3 border rounded-md overflow-hidden">
              <button
                className="flex w-full items-center gap-2 px-3 py-2 hover:bg-muted/50 text-left"
                onClick={() => setMemorySectionOpen(!memorySectionOpen)}
              >
                {memorySectionOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                <Brain className="h-3 w-3 text-purple-400" />
                <span className="flex-1 text-xs font-medium">Memories</span>
                {memoryLoading && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-purple-500/10 text-purple-500">
                  {memoryResults.length}
                </Badge>
              </button>
              {memorySectionOpen && (
                <div className="space-y-1 px-3 pb-2">
                  {memoryResults.length === 0 && !memoryLoading && (
                    <p className="text-[11px] text-muted-foreground py-1">No relevant memories</p>
                  )}
                  {memoryResults.map((m, i) => (
                    <div key={`mem-${m.memory_id}-${i}`} className="flex items-start gap-2 rounded-md border px-2.5 py-1.5">
                      <Brain className={cn("h-3 w-3 shrink-0 mt-0.5", {
                        "text-blue-400": m.memory_type === "empirical",
                        "text-amber-400": m.memory_type === "decision",
                        "text-green-400": m.memory_type === "preference",
                        "text-purple-400": m.memory_type === "project_context",
                        "text-orange-400": m.memory_type === "temporal",
                        "text-cyan-400": m.memory_type === "conversational",
                      })} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1">
                          <p className="truncate text-[11px] font-medium">{m.summary || m.content.slice(0, 60)}</p>
                          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{Math.round(m.relevance * 100)}%</span>
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                          <Badge variant="outline" className="text-[9px] px-1 py-0">{m.memory_type}</Badge>
                          <span>{Math.round(m.age_days)}d</span>
                        </div>
                      </div>
                      <TooltipProvider delayDuration={0}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-6 w-6 shrink-0"
                              onClick={() => injectResult({
                                artifact_id: m.memory_id,
                                filename: `Memory: ${m.memory_type}`,
                                domain: "conversations",
                                relevance: m.relevance,
                                chunk_index: 0,
                                content: m.content,
                                collection: "memories",
                                ingested_at: "",
                              })}
                            >
                              <Plus className="h-3 w-3" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Inject into chat</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* External sources section */}
          {hasQueried && externalResults.length > 0 && (
            <div className="mt-2 border rounded-md overflow-hidden">
              <button
                className="flex w-full items-center gap-2 px-3 py-2 hover:bg-muted/50 text-left"
                onClick={() => setExternalSectionOpen(!externalSectionOpen)}
              >
                {externalSectionOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                <Globe className="h-3 w-3 text-green-400" />
                <span className="flex-1 text-xs font-medium">External</span>
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-green-500/10 text-green-500">
                  {externalResults.length}
                </Badge>
              </button>
              {externalSectionOpen && (
                <div className="space-y-1 px-3 pb-2">
                  {externalResults.map((result) => (
                    <div key={`ext-${result.artifact_id}-${result.chunk_index}`} className="flex items-start gap-2 rounded-md border px-2.5 py-1.5">
                      <Globe className="h-3 w-3 shrink-0 text-green-400 mt-0.5" />
                      <div className="min-w-0 flex-1">
                        <p className="text-[11px] font-medium">{result.filename}</p>
                        <p className="mt-0.5 text-[10px] text-muted-foreground line-clamp-2">{result.content}</p>
                        {result.source_url && (
                          <a href={result.source_url} target="_blank" rel="noopener noreferrer" className="mt-0.5 inline-block text-[10px] text-primary hover:underline">
                            Source &rarr;
                          </a>
                        )}
                      </div>
                      <TooltipProvider delayDuration={0}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-6 w-6 shrink-0"
                              onClick={() => injectResult(result)}
                            >
                              <Plus className="h-3 w-3" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Inject into chat</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Graph preview for selected artifact */}
        {selectedArtifactId && (
          <>
            <Separator />
            <GraphPreview artifactId={selectedArtifactId} />
          </>
        )}
      </ScrollArea>

      {/* KB Dashboard Footer */}
      <div className="border-t bg-muted/30">
        {/* Injected count */}
        {injectedContext.length > 0 && (
          <div className="border-b px-3 py-1.5 text-xs text-muted-foreground">
            {injectedContext.length} source{injectedContext.length !== 1 ? "s" : ""} ready to inject
          </div>
        )}

        {/* KB Metrics */}
        {kbStats && (
          <div className="grid grid-cols-3 gap-px px-3 py-2">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <FileText className="h-3 w-3 shrink-0 text-teal-500" />
              <span className="tabular-nums font-medium text-foreground">{kbStats.total_artifacts}</span>
              <span className="hidden min-[320px]:inline">docs</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Layers className="h-3 w-3 shrink-0 text-teal-500" />
              <span className="tabular-nums font-medium text-foreground">{kbStats.total_chunks}</span>
              <span className="hidden min-[320px]:inline">vectors</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Database className="h-3 w-3 shrink-0 text-teal-500" />
              <span className="tabular-nums font-medium text-foreground">{Object.keys(kbStats.domains).length}</span>
              <span className="hidden min-[320px]:inline">domains</span>
            </div>
          </div>
        )}

        {/* Storage mode toggle + drag-drop hint */}
        <div className="flex items-center justify-between gap-2 border-t px-3 py-2">
          <TooltipProvider delayDuration={0}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] transition-colors",
                    archiveMode
                      ? "bg-teal-500/10 text-teal-500 border border-teal-500/30"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted",
                  )}
                  onClick={() => setArchiveMode(!archiveMode)}
                >
                  {archiveMode ? <Archive className="h-3 w-3" /> : <FileInput className="h-3 w-3" />}
                  {archiveMode ? "Archive" : "Extract only"}
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-[200px] text-xs">
                {archiveMode
                  ? "Files are archived to ~/cerid-archive/ and extracted for KB"
                  : "Files are parsed for KB data only — originals are not stored"}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <div className="flex flex-col items-end gap-0.5">
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Upload className="h-3 w-3" />
              Drop files to ingest
            </span>
            <span className="text-[10px] text-muted-foreground/40">
              PDF &bull; DOCX &bull; TXT &bull; CSV &bull; JSON &bull; EPUB
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}