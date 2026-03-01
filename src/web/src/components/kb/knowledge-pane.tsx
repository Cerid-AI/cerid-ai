// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import { Search, X, Loader2, AlertCircle, RefreshCcw, Upload, CheckCircle, Tag } from "lucide-react"
import { ArtifactCard } from "./artifact-card"
import { TaxonomyTree } from "./taxonomy-tree"
import { GraphPreview } from "./graph-preview"
import { fetchArtifacts, queryKB, uploadFile } from "@/lib/api"
import { useKBInjection } from "@/contexts/kb-injection-context"
import type { KBQueryResult, Artifact } from "@/lib/types"

function parseJsonArray(json: string | undefined): string[] {
  if (!json) return []
  try {
    const arr = JSON.parse(json)
    return Array.isArray(arr) ? arr.filter((s): s is string => typeof s === "string" && s.trim().length > 0) : []
  } catch { return [] }
}

function artifactToResult(a: Artifact): KBQueryResult {
  // Parse keywords to use as tags when the artifact has no tags
  const tags = a.tags && a.tags.length > 0
    ? a.tags
    : parseJsonArray(a.keywords).slice(0, 5)

  return {
    content: a.summary || "",
    relevance: 0,
    artifact_id: a.id,
    filename: a.filename,
    domain: a.domain,
    sub_category: a.sub_category,
    tags,
    chunk_index: 0,
    collection: `domain_${a.domain}`,
    ingested_at: a.ingested_at,
  }
}

/** Deduplicate search results by artifact_id, keeping the highest-relevance chunk. */
function deduplicateByArtifact(results: KBQueryResult[]): KBQueryResult[] {
  const best = new Map<string, KBQueryResult>()
  for (const r of results) {
    const existing = best.get(r.artifact_id)
    if (!existing || r.relevance > existing.relevance) {
      best.set(r.artifact_id, r)
    }
  }
  return [...best.values()]
}

export function KnowledgePane() {
  const { injectResult, injectedContext } = useKBInjection()
  const [searchInput, setSearchInput] = useState("")
  const [activeSearch, setActiveSearch] = useState("")
  const [taxonomyFilter, setTaxonomyFilter] = useState<{ domain: string | null; subCategory: string | null }>({
    domain: null,
    subCategory: null,
  })
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null)
  const [activeTag, setActiveTag] = useState<string | null>(null)
  const [uploadStatus, setUploadStatus] = useState<"idle" | "uploading" | "success" | "error">("idle")
  const [uploadMessage, setUploadMessage] = useState("")
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const activeDomain = taxonomyFilter.domain

  const handleFileUpload = useCallback(async (file: File) => {
    setUploadStatus("uploading")
    setUploadMessage("")
    try {
      const domain = activeDomain ?? undefined
      const result = await uploadFile(file, { domain })
      setUploadStatus("success")
      setUploadMessage(`${result.filename} ingested (${result.chunks} chunks)`)
      queryClient.invalidateQueries({ queryKey: ["artifacts"] })
      setTimeout(() => setUploadStatus("idle"), 4000)
    } catch (err) {
      setUploadStatus("error")
      setUploadMessage(err instanceof Error ? err.message : "Upload failed")
      setTimeout(() => setUploadStatus("idle"), 5000)
    }
  }, [activeDomain, queryClient])

  const {
    data: artifacts,
    isLoading: browsing,
    isError: browseError,
    error: browseErrorDetail,
    refetch: refetchArtifacts,
  } = useQuery({
    queryKey: ["artifacts", activeDomain ?? "all"],
    queryFn: () => fetchArtifacts(activeDomain ?? undefined, 200),
    enabled: !activeSearch,
    staleTime: 30_000,
    retry: 1,
  })

  const {
    data: searchResults,
    isLoading: searching,
    isError: searchError,
    error: searchErrorDetail,
    refetch: refetchSearch,
  } = useQuery({
    queryKey: ["kb-search", activeSearch, activeDomain ?? "all"],
    queryFn: () =>
      queryKB(activeSearch, activeDomain ? [activeDomain] : undefined),
    enabled: !!activeSearch && activeSearch.length > 2,
    staleTime: 60_000,
    retry: 1,
  })

  const isLoading = activeSearch ? searching : browsing
  const isError = activeSearch ? searchError : browseError
  const errorDetail = activeSearch ? searchErrorDetail : browseErrorDetail
  const refetch = activeSearch ? refetchSearch : refetchArtifacts

  const allResults: KBQueryResult[] = activeSearch
    ? deduplicateByArtifact(searchResults?.results ?? [])
    : (artifacts ?? [])
        .filter((a) => !activeDomain || a.domain === activeDomain)
        .filter((a) => !taxonomyFilter.subCategory || a.sub_category === taxonomyFilter.subCategory)
        .map(artifactToResult)

  // Compute artifact counts per domain for the taxonomy tree
  const domainCounts = useMemo(() => {
    if (activeSearch || !artifacts) return new Map<string, number>()
    const counts = new Map<string, number>()
    for (const a of artifacts) {
      counts.set(a.domain, (counts.get(a.domain) ?? 0) + 1)
    }
    return counts
  }, [artifacts, activeSearch])

  const availableTags = useMemo(() => {
    const tagCounts = new Map<string, number>()
    for (const r of allResults) {
      for (const tag of r.tags ?? []) {
        tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1)
      }
    }
    return [...tagCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)
  }, [allResults])

  const results = activeTag
    ? allResults.filter((r) => r.tags?.includes(activeTag))
    : allResults

  const executeSearch = useCallback(() => {
    if (searchInput.trim().length > 2) {
      setActiveSearch(searchInput.trim())
    }
  }, [searchInput])

  const clearSearch = useCallback(() => {
    setSearchInput("")
    setActiveSearch("")
  }, [])

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Knowledge Base</h2>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            disabled={uploadStatus === "uploading"}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploadStatus === "uploading" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Upload className="mr-1 h-3 w-3" />
            )}
            Upload
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            className="sr-only"
            aria-label="Upload file"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleFileUpload(file)
              e.target.value = ""
            }}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          {activeSearch
            ? `${results.length} results for "${activeSearch}"`
            : `${results.length} artifacts`}
          {taxonomyFilter.subCategory && (
            <span className="ml-1 text-primary">
              in {taxonomyFilter.subCategory}
            </span>
          )}
        </p>
        {uploadStatus !== "idle" && (
          <div className={`mt-1 flex items-center gap-1 text-xs ${uploadStatus === "error" ? "text-destructive" : uploadStatus === "success" ? "text-green-500" : "text-muted-foreground"}`}>
            {uploadStatus === "success" && <CheckCircle className="h-3 w-3" />}
            {uploadStatus === "error" && <AlertCircle className="h-3 w-3" />}
            {uploadStatus === "uploading" && <Loader2 className="h-3 w-3 animate-spin" />}
            {uploadMessage}
          </div>
        )}
      </div>

      {/* Search + filters */}
      <div className="space-y-2 border-b px-4 py-3">
        <div className="flex min-w-0 gap-1.5">
          <Input
            placeholder="Search artifacts..."
            aria-label="Search artifacts"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") executeSearch()
              if (e.key === "Escape") clearSearch()
            }}
            className="h-9"
          />
          {activeSearch ? (
            <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" onClick={clearSearch} aria-label="Clear search">
              <X className="h-4 w-4" />
            </Button>
          ) : (
            <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" onClick={executeSearch} aria-label="Search">
              <Search className="h-4 w-4" />
            </Button>
          )}
        </div>
        <TaxonomyTree
          filter={taxonomyFilter}
          onFilterChange={setTaxonomyFilter}
          artifactCounts={domainCounts}
        />
        {availableTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <Tag className="h-3 w-3 shrink-0 text-muted-foreground" />
            {availableTags.map(([tag, count]) => (
              <Badge
                key={tag}
                variant={activeTag === tag ? "default" : "secondary"}
                className="cursor-pointer text-[10px]"
                role="button"
                tabIndex={0}
                aria-pressed={activeTag === tag}
                onClick={() => setActiveTag(activeTag === tag ? null : tag)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setActiveTag(activeTag === tag ? null : tag) } }}
              >
                {tag} ({count})
              </Badge>
            ))}
            {activeTag && (
              <Button variant="ghost" size="xs" className="h-5 text-[10px]" onClick={() => setActiveTag(null)}>
                Clear
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Results */}
      <ScrollArea className="flex-1">
        <div className="min-w-0 space-y-2 overflow-hidden p-4">
          {isLoading && (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              <span className="text-sm">{activeSearch ? "Searching..." : "Loading artifacts..."}</span>
            </div>
          )}

          {!isLoading && isError && (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <AlertCircle className="h-8 w-8 text-destructive" />
              <div>
                <p className="text-sm font-medium text-destructive">Failed to load artifacts</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {errorDetail instanceof Error ? errorDetail.message : "Connection error — check backend services"}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={() => refetch()}>
                <RefreshCcw className="mr-1.5 h-3 w-3" />
                Retry
              </Button>
            </div>
          )}

          {!isLoading && !isError && results.length === 0 && (
            <div className="py-12 text-center text-sm text-muted-foreground">
              {activeSearch ? "No results found" : "No artifacts in the knowledge base"}
            </div>
          )}

          {results.map((result, i) => (
            <ArtifactCard
              key={`${result.artifact_id}-${result.chunk_index}-${i}`}
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

      {injectedContext.length > 0 && (
        <div className="border-t px-4 py-1.5 text-xs text-muted-foreground">
          {injectedContext.length} source{injectedContext.length !== 1 ? "s" : ""} ready — switch to Chat to use
        </div>
      )}
    </div>
  )
}

export default KnowledgePane
