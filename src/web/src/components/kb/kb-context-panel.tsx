// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { X, Search, Loader2, Zap, Upload } from "lucide-react"
import { ArtifactCard } from "./artifact-card"
import { DomainFilter } from "./domain-filter"
import { TagFilter } from "./tag-filter"
import { GraphPreview } from "./graph-preview"
import { UploadDialog } from "./upload-dialog"
import { useSettings } from "@/hooks/use-settings"
import { useDragDrop } from "@/hooks/use-drag-drop"
import type { UseKBContextReturn } from "@/hooks/use-kb-context"
import { uploadFile } from "@/lib/api"
import { cn } from "@/lib/utils"

interface KBContextPanelProps extends UseKBContextReturn {
  onClose: () => void
}

export function KBContextPanel({
  results,
  confidence,
  totalResults,
  isLoading,
  error,
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

  // Drag-drop for file ingestion
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const { isDragOver, dragHandlers } = useDragDrop(setPendingFiles)

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
            <span className="text-sm font-medium">Drop to ingest & inject</span>
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
        {totalResults > 0 && (
          <span className="text-xs text-muted-foreground">{totalResults} results</span>
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

      {/* Confidence bar */}
      {totalResults > 0 && (
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex items-center gap-2 px-3 py-1.5">
                <span className="text-xs text-muted-foreground">Confidence</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all"
                    style={{ width: `${confidencePct}%` }}
                  />
                </div>
                <span className="text-xs font-medium tabular-nums">{confidencePct}%</span>
              </div>
            </TooltipTrigger>
            <TooltipContent>Confidence: {confidencePct}% retrieval confidence</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}

      {/* Results */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="min-w-0 space-y-2 p-3">
          {isLoading && (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              <span className="text-sm">Searching knowledge base...</span>
            </div>
          )}

          {error && (
            <div className="py-4 text-center text-sm text-destructive">
              Failed to query knowledge base
            </div>
          )}

          {!isLoading && !error && results.length === 0 && (
            <div className="py-8 text-center text-sm text-muted-foreground">
              {hasQueried ? "No matching knowledge found" : "Send a message to see related knowledge"}
            </div>
          )}

          {results.map((result) => (
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
        </div>

        {/* Graph preview for selected artifact */}
        {selectedArtifactId && (
          <>
            <Separator />
            <GraphPreview artifactId={selectedArtifactId} />
          </>
        )}
      </ScrollArea>

      {/* Injected count */}
      {injectedContext.length > 0 && (
        <div className="border-t px-3 py-1.5 text-xs text-muted-foreground">
          {injectedContext.length} source{injectedContext.length !== 1 ? "s" : ""} ready to inject
        </div>
      )}
    </div>
  )
}