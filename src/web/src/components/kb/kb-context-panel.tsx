// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { X, Search, Loader2 } from "lucide-react"
import { ArtifactCard } from "./artifact-card"
import { DomainFilter } from "./domain-filter"
import { TagFilter } from "./tag-filter"
import { GraphPreview } from "./graph-preview"
import type { UseKBContextReturn } from "@/hooks/use-kb-context"

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

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="flex-1 text-sm font-medium">Knowledge Context</span>
        {totalResults > 0 && (
          <span className="text-xs text-muted-foreground">{totalResults} results</span>
        )}
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
      )}

      {/* Results */}
      <ScrollArea className="flex-1">
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