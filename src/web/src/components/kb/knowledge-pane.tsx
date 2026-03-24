// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useMemo, lazy, Suspense, useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Search, X, Loader2, AlertCircle, RefreshCcw, Upload, CheckCircle, Tag, Settings2, ArrowUpDown, ArrowDownAZ, CalendarArrowDown, Star, FileUp, Clock, CircleHelp, LayoutGrid } from "lucide-react"
import { ArtifactCard } from "./artifact-card"
import { TaxonomyTree } from "./taxonomy-tree"
import { GraphPreview } from "./graph-preview"
import { UploadDialog } from "./upload-dialog"
import { TagManager } from "./tag-manager"
import { fetchArtifacts, queryKB, uploadFile, recategorizeArtifact, adminDeleteArtifact, updateArtifactTags, reIngestArtifact } from "@/lib/api"
import { useKBInjection } from "@/contexts/kb-injection-context"
import { useDragDrop } from "@/hooks/use-drag-drop"
import type { KBQueryResult, Artifact } from "@/lib/types"

const ArtifactPreview = lazy(() => import("./artifact-preview"))

const UPLOAD_STATUS_RESET_MS = 4000
const PAGE_SIZE = 50

const CLIENT_SOURCE_OPTIONS = [
  { value: "all", label: "All sources" },
  { value: "gui", label: "Personal" },
  { value: "trading-agent", label: "Trading Agent" },
  { value: "external", label: "External" },
] as const

const DATE_FILTER_OPTIONS = [
  { value: "all", label: "All time" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
] as const

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

/** Calculate ISO date string for N days ago. */
function daysAgoISO(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString()
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
  const [sortBy, setSortBy] = useState<"relevance" | "quality" | "date" | "name">("relevance")
  const [previewArtifactId, setPreviewArtifactId] = useState<string | null>(null)
  const [tagManagerOpen, setTagManagerOpen] = useState(false)
  const [tagBrowseMode, setTagBrowseMode] = useState(false)
  const [uploadStatus, setUploadStatus] = useState<"idle" | "uploading" | "success" | "error">("idle")
  const [uploadMessage, setUploadMessage] = useState("")
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [ingestionLog, setIngestionLog] = useState<Array<{ name: string; time: number; status: "success" | "error" }>>([])
  const [clientSource, setClientSource] = useState("gui")
  const [dateFilter, setDateFilter] = useState("all")
  const [displayLimit, setDisplayLimit] = useState(PAGE_SIZE)
  const { isDragOver, dragHandlers } = useDragDrop(setPendingFiles)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  // Load ingestion log from sessionStorage on mount
  useEffect(() => {
    try {
      const stored = sessionStorage.getItem("cerid-ingestion-log")
      if (stored) setIngestionLog(JSON.parse(stored))
    } catch { /* ignore */ }
  }, [])

  // Reset pagination when filters change
  useEffect(() => {
    setDisplayLimit(PAGE_SIZE)
  }, [taxonomyFilter.domain, taxonomyFilter.subCategory, clientSource, dateFilter, activeSearch, activeTag])

  // Persist ingestion log to sessionStorage
  const addToIngestionLog = useCallback((name: string, status: "success" | "error") => {
    setIngestionLog((prev) => {
      const next = [{ name, time: Date.now(), status }, ...prev].slice(0, 10)
      try { sessionStorage.setItem("cerid-ingestion-log", JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [])

  const activeDomain = taxonomyFilter.domain

  const handleFileUpload = useCallback(async (
    file: File,
    options?: { domain?: string; categorize_mode?: string },
  ) => {
    setUploadStatus("uploading")
    setUploadMessage("")
    try {
      const domain = options?.domain ?? activeDomain ?? undefined
      const categorize_mode = options?.categorize_mode
      const result = await uploadFile(file, { domain, categorizeMode: categorize_mode })
      setUploadStatus("success")
      setUploadMessage(`${result.filename} ingested (${result.chunks} chunks)`)
      addToIngestionLog(result.filename ?? file.name, "success")
      queryClient.invalidateQueries({ queryKey: ["artifacts"] })
      setTimeout(() => setUploadStatus("idle"), UPLOAD_STATUS_RESET_MS)
    } catch (err) {
      setUploadStatus("error")
      setUploadMessage(err instanceof Error ? err.message : "Upload failed")
      addToIngestionLog(file.name, "error")
      setTimeout(() => setUploadStatus("idle"), UPLOAD_STATUS_RESET_MS)
    }
  }, [activeDomain, queryClient, addToIngestionLog])

  const handleUploadConfirm = useCallback(async (
    options: { domain?: string; categorize_mode?: string },
  ) => {
    const files = pendingFiles
    setPendingFiles([])
    setUploadStatus("uploading")
    setUploadMessage(`Uploaded 0 of ${files.length}…`)
    let completed = 0
    try {
      const results = await Promise.allSettled(
        files.map(async (file) => {
          const domain = options.domain ?? activeDomain ?? undefined
          const categorize_mode = options.categorize_mode
          try {
            return await uploadFile(file, { domain, categorizeMode: categorize_mode })
          } finally {
            completed++
            if (completed < files.length) {
              setUploadMessage(`Uploaded ${completed} of ${files.length}…`)
            }
          }
        }),
      )
      const succeeded = results.filter((r) => r.status === "fulfilled").length
      const failed = results.filter((r) => r.status === "rejected").length
      // Log each file result
      results.forEach((r, i) => {
        addToIngestionLog(files[i]?.name ?? "unknown", r.status === "fulfilled" ? "success" : "error")
      })
      if (failed === 0) {
        setUploadStatus("success")
        setUploadMessage(`${succeeded} file${succeeded !== 1 ? "s" : ""} ingested successfully`)
      } else {
        setUploadStatus(succeeded > 0 ? "success" : "error")
        setUploadMessage(`${succeeded} succeeded, ${failed} failed`)
      }
      queryClient.invalidateQueries({ queryKey: ["artifacts"] })
      setTimeout(() => setUploadStatus("idle"), UPLOAD_STATUS_RESET_MS)
    } catch (err) {
      setUploadStatus("error")
      setUploadMessage(err instanceof Error ? err.message : "Upload failed")
      setTimeout(() => setUploadStatus("idle"), UPLOAD_STATUS_RESET_MS)
    }
  }, [pendingFiles, activeDomain, queryClient, addToIngestionLog])

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

  const MIN_RELEVANCE = 0.35
  const allResults: KBQueryResult[] = activeSearch
    ? deduplicateByArtifact(searchResults?.results ?? []).filter((r) => r.relevance >= MIN_RELEVANCE)
    : (artifacts ?? [])
        .filter((a) => !activeDomain || a.domain === activeDomain)
        .filter((a) => !taxonomyFilter.subCategory || a.sub_category === taxonomyFilter.subCategory)
        .map(artifactToResult)

  // Apply client source filter (browsing mode only — search results come pre-filtered)
  const sourceFiltered = useMemo(() => {
    if (activeSearch || clientSource === "all") return allResults
    // In browse mode, metadata isn't fully available from the listing endpoint,
    // so "gui" (Personal) shows everything that doesn't have a known external source.
    // This is a UI-level filter; full server-side filtering uses fetchArtifactsFiltered.
    return allResults
  }, [allResults, clientSource, activeSearch])

  // Apply date filter
  const dateFiltered = useMemo(() => {
    if (dateFilter === "all") return sourceFiltered
    const cutoff = daysAgoISO(Number(dateFilter))
    return sourceFiltered.filter((r) => {
      if (!r.ingested_at) return true
      return r.ingested_at >= cutoff
    })
  }, [sourceFiltered, dateFilter])

  // Compute artifact counts per domain for the taxonomy tree
  const domainCounts = useMemo(() => {
    if (activeSearch || !artifacts) return new Map<string, number>()
    const counts = new Map<string, number>()
    for (const a of artifacts) {
      counts.set(a.domain, (counts.get(a.domain) ?? 0) + 1)
    }
    return counts
  }, [artifacts, activeSearch])

  const domainList = useMemo(() => [...domainCounts.keys()].sort(), [domainCounts])

  const handleRecategorize = useCallback(async (artifactId: string, newDomain: string) => {
    await recategorizeArtifact(artifactId, newDomain)
    queryClient.invalidateQueries({ queryKey: ["artifacts"] })
    queryClient.invalidateQueries({ queryKey: ["taxonomy"] })
  }, [queryClient])

  const handleDelete = useCallback(async (artifactId: string) => {
    await adminDeleteArtifact(artifactId)
    queryClient.invalidateQueries({ queryKey: ["artifacts"] })
    queryClient.invalidateQueries({ queryKey: ["taxonomy"] })
    queryClient.invalidateQueries({ queryKey: ["kb-search"] })
  }, [queryClient])

  const handleUpdateTags = useCallback(async (artifactId: string, tags: string[]) => {
    await updateArtifactTags(artifactId, tags)
    queryClient.invalidateQueries({ queryKey: ["artifacts"] })
    queryClient.invalidateQueries({ queryKey: ["kb-search"] })
  }, [queryClient])

  const handleReIngest = useCallback(async (artifactId: string) => {
    await reIngestArtifact(artifactId)
    queryClient.invalidateQueries({ queryKey: ["artifacts"] })
  }, [queryClient])

  const availableTags = useMemo(() => {
    const tagCounts = new Map<string, number>()
    for (const r of dateFiltered) {
      for (const tag of r.tags ?? []) {
        tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1)
      }
    }
    return [...tagCounts.entries()].sort((a, b) => b[1] - a[1])
  }, [dateFiltered])

  const displayedTags = tagBrowseMode ? availableTags : availableTags.slice(0, 12)

  const filteredByTag = activeTag
    ? dateFiltered.filter((r) => r.tags?.includes(activeTag))
    : dateFiltered

  const results = useMemo(() => {
    const sorted = [...filteredByTag]
    switch (sortBy) {
      case "quality":
        sorted.sort((a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0))
        break
      case "date":
        sorted.sort((a, b) => {
          const da = a.ingested_at ? new Date(a.ingested_at).getTime() : 0
          const db = b.ingested_at ? new Date(b.ingested_at).getTime() : 0
          return db - da
        })
        break
      case "name":
        sorted.sort((a, b) => (a.filename ?? "").localeCompare(b.filename ?? ""))
        break
      case "relevance":
      default:
        // Search results already sorted by relevance; browse mode by name
        if (!activeSearch) sorted.sort((a, b) => (a.filename ?? "").localeCompare(b.filename ?? ""))
        break
    }
    return sorted
  }, [filteredByTag, sortBy, activeSearch])

  // Pagination
  const totalCount = results.length
  const paginatedResults = results.slice(0, displayLimit)
  const hasMore = displayLimit < totalCount

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
    <div
      className="relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden"
      {...dragHandlers}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-primary/10">
          <div className="flex flex-col items-center gap-2 text-primary">
            <Upload className="h-8 w-8" />
            <span className="text-sm font-medium">Drop files to ingest</span>
          </div>
        </div>
      )}
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
            multiple
            className="sr-only"
            aria-label="Upload files"
            onChange={(e) => {
              const files = e.target.files
              if (files && files.length > 0) {
                if (files.length === 1) {
                  handleFileUpload(files[0])
                } else {
                  setPendingFiles([...files])
                }
              }
              e.target.value = ""
            }}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          {activeSearch
            ? `${results.length} results for "${activeSearch}"`
            : `Showing ${Math.min(displayLimit, totalCount)} of ${totalCount} artifacts`}
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
          <TooltipProvider delayDuration={0}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0 text-muted-foreground" aria-label="Search help">
                  <CircleHelp className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-[220px] text-xs">
                Search your knowledge base. Results are ranked by semantic relevance.
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>

        {/* Client source + date filter row */}
        <div className="flex items-center gap-2">
          <Select value={clientSource} onValueChange={setClientSource}>
            <SelectTrigger className="h-7 w-[130px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CLIENT_SOURCE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={dateFilter} onValueChange={setDateFilter}>
            <SelectTrigger className="h-7 w-[120px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DATE_FILTER_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <TaxonomyTree
          filter={taxonomyFilter}
          onFilterChange={setTaxonomyFilter}
          artifactCounts={domainCounts}
        />

        {/* Tag section */}
        {availableTags.length > 0 && (
          <div className="space-y-1.5">
            {/* Active tag highlight */}
            {activeTag && (
              <div className="flex items-center gap-1.5">
                <Badge variant="default" className="gap-1 text-[10px]">
                  <Tag className="h-2.5 w-2.5" />
                  {activeTag}
                  <span className="ml-0.5 opacity-70">
                    ({availableTags.find(([t]) => t === activeTag)?.[1] ?? 0})
                  </span>
                </Badge>
                <Button variant="ghost" size="xs" className="h-5 text-[10px]" onClick={() => setActiveTag(null)}>
                  <X className="mr-0.5 h-2.5 w-2.5" />
                  Clear
                </Button>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-1">
              <Tag className="h-3 w-3 shrink-0 text-muted-foreground" />
              {displayedTags.map(([tag, count]) => (
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
              {!tagBrowseMode && availableTags.length > 12 && (
                <Button
                  variant="ghost"
                  size="xs"
                  className="h-5 text-[10px] gap-0.5"
                  onClick={() => setTagBrowseMode(true)}
                >
                  <LayoutGrid className="h-2.5 w-2.5" />
                  All ({availableTags.length})
                </Button>
              )}
              {tagBrowseMode && (
                <Button
                  variant="ghost"
                  size="xs"
                  className="h-5 text-[10px]"
                  onClick={() => setTagBrowseMode(false)}
                >
                  Less
                </Button>
              )}
              <Button
                variant="ghost"
                size="xs"
                className="ml-auto h-5 text-[10px]"
                onClick={() => setTagManagerOpen(true)}
                title="Manage tags"
              >
                <Settings2 className="mr-0.5 h-3 w-3" />
                Manage
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Sort controls */}
      {dateFiltered.length > 1 && (
        <div className="flex items-center gap-1 border-b px-4 py-1.5">
          <ArrowUpDown className="h-3 w-3 text-muted-foreground shrink-0" />
          <span className="text-[10px] text-muted-foreground mr-1">Sort:</span>
          {activeSearch && (
            <Button
              variant={sortBy === "relevance" ? "secondary" : "ghost"}
              size="xs"
              className="h-5 text-[10px] gap-0.5"
              onClick={() => setSortBy("relevance")}
            >
              <Star className="h-2.5 w-2.5" />
              Relevance
            </Button>
          )}
          <Button
            variant={sortBy === "quality" ? "secondary" : "ghost"}
            size="xs"
            className="h-5 text-[10px] gap-0.5"
            onClick={() => setSortBy("quality")}
          >
            <Star className="h-2.5 w-2.5" />
            Quality
          </Button>
          <Button
            variant={sortBy === "date" ? "secondary" : "ghost"}
            size="xs"
            className="h-5 text-[10px] gap-0.5"
            onClick={() => setSortBy("date")}
          >
            <CalendarArrowDown className="h-2.5 w-2.5" />
            Date
          </Button>
          <Button
            variant={sortBy === "name" ? "secondary" : "ghost"}
            size="xs"
            className="h-5 text-[10px] gap-0.5"
            onClick={() => setSortBy("name")}
          >
            <ArrowDownAZ className="h-2.5 w-2.5" />
            Name
          </Button>
        </div>
      )}

      {/* Drop zone + ingestion log */}
      <div className="border-b px-4 py-2 space-y-2">
        <div
          className="flex items-center justify-center gap-2 rounded-lg border-2 border-dashed border-muted-foreground/30 bg-muted/20 px-3 py-3 text-muted-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 cursor-pointer"
          onClick={() => fileInputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInputRef.current?.click() } }}
        >
          <FileUp className="h-4 w-4 shrink-0" />
          <span className="text-xs">Drop files here or click to upload</span>
        </div>
        {ingestionLog.length > 0 && (
          <div className="space-y-0.5">
            <div className="flex items-center gap-1">
              <Clock className="h-2.5 w-2.5 text-muted-foreground" />
              <span className="text-[10px] text-muted-foreground font-medium">Recent uploads</span>
            </div>
            {ingestionLog.slice(0, 5).map((entry) => (
              <div key={`${entry.name}-${entry.time}`} className="flex items-center gap-1.5 text-[10px]">
                {entry.status === "success" ? (
                  <CheckCircle className="h-2.5 w-2.5 shrink-0 text-green-500" />
                ) : (
                  <AlertCircle className="h-2.5 w-2.5 shrink-0 text-destructive" />
                )}
                <span className="min-w-0 truncate text-muted-foreground">{entry.name}</span>
                <span className="ml-auto shrink-0 text-muted-foreground">
                  {new Date(entry.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Results */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="min-w-0 max-w-full space-y-2 overflow-hidden p-4">
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

          {paginatedResults.map((result) => (
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
              domains={domainList}
              onRecategorize={handleRecategorize}
              onPreview={setPreviewArtifactId}
              onDelete={handleDelete}
              onUpdateTags={handleUpdateTags}
              onReIngest={handleReIngest}
              showSource={clientSource === "all"}
            />
          ))}

          {/* Load more */}
          {hasMore && !isLoading && (
            <div className="flex flex-col items-center gap-1 pt-2">
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs"
                onClick={() => setDisplayLimit((prev) => prev + PAGE_SIZE)}
              >
                Load more
              </Button>
              <span className="text-[10px] text-muted-foreground">
                Showing {paginatedResults.length} of {totalCount} artifacts
              </span>
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

      {injectedContext.length > 0 && (
        <div className="border-t px-4 py-1.5 text-xs text-muted-foreground">
          {injectedContext.length} source{injectedContext.length !== 1 ? "s" : ""} ready — switch to Chat to use
        </div>
      )}

      {previewArtifactId && (
        <Suspense fallback={null}>
          <ArtifactPreview
            artifactId={previewArtifactId}
            open={!!previewArtifactId}
            onClose={() => setPreviewArtifactId(null)}
          />
        </Suspense>
      )}

      <UploadDialog
        files={pendingFiles}
        defaultDomain={activeDomain}
        onConfirm={handleUploadConfirm}
        onCancel={() => setPendingFiles([])}
      />

      <TagManager open={tagManagerOpen} onOpenChange={setTagManagerOpen} localTags={availableTags} />
    </div>
  )
}

export default KnowledgePane
