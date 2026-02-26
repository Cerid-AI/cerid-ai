import { useState, useCallback, useRef, useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import { Search, X, Loader2, AlertCircle, RefreshCcw, Upload, CheckCircle, Tag } from "lucide-react"
import { ArtifactCard } from "./artifact-card"
import { DomainFilter } from "./domain-filter"
import { GraphPreview } from "./graph-preview"
import { fetchArtifacts, queryKB, uploadFile } from "@/lib/api"
import { useKBInjection } from "@/contexts/kb-injection-context"
import type { KBQueryResult, Artifact } from "@/lib/types"

function parseTags(tags: string[] | string | undefined): string[] {
  if (!tags) return []
  if (Array.isArray(tags)) return tags
  try { return JSON.parse(tags) } catch { return [] }
}

function artifactToResult(a: Artifact): KBQueryResult {
  return {
    content: a.summary || "",
    relevance: 0,
    artifact_id: a.id,
    filename: a.filename,
    domain: a.domain,
    sub_category: a.sub_category,
    tags: parseTags(a.tags),
    chunk_index: 0,
    collection: `domain_${a.domain}`,
    ingested_at: a.ingested_at,
  }
}

export function KnowledgePane() {
  const { injectResult, injectedContext } = useKBInjection()
  const [searchInput, setSearchInput] = useState("")
  const [activeSearch, setActiveSearch] = useState("")
  const [activeDomains, setActiveDomains] = useState<Set<string>>(new Set())
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null)
  const [activeTag, setActiveTag] = useState<string | null>(null)
  const [uploadStatus, setUploadStatus] = useState<"idle" | "uploading" | "success" | "error">("idle")
  const [uploadMessage, setUploadMessage] = useState("")
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const handleFileUpload = useCallback(async (file: File) => {
    setUploadStatus("uploading")
    setUploadMessage("")
    try {
      const domain = activeDomains.size === 1 ? [...activeDomains][0] : undefined
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
  }, [activeDomains, queryClient])

  const domainKey = [...activeDomains].sort().join(",")

  // Browse mode: list recent artifacts
  const {
    data: artifacts,
    isLoading: browsing,
    isError: browseError,
    error: browseErrorDetail,
    refetch: refetchArtifacts,
  } = useQuery({
    queryKey: ["artifacts", domainKey],
    queryFn: () => {
      const domain = activeDomains.size === 1 ? [...activeDomains][0] : undefined
      return fetchArtifacts(domain, 200)
    },
    enabled: !activeSearch,
    staleTime: 30_000,
    retry: 1,
  })

  // Search mode: query KB
  const {
    data: searchResults,
    isLoading: searching,
    isError: searchError,
    error: searchErrorDetail,
    refetch: refetchSearch,
  } = useQuery({
    queryKey: ["kb-search", activeSearch, [...activeDomains].sort().join(",")],
    queryFn: () =>
      queryKB(activeSearch, activeDomains.size > 0 ? [...activeDomains] : undefined),
    enabled: !!activeSearch && activeSearch.length > 2,
    staleTime: 60_000,
    retry: 1,
  })

  const isLoading = activeSearch ? searching : browsing
  const isError = activeSearch ? searchError : browseError
  const errorDetail = activeSearch ? searchErrorDetail : browseErrorDetail
  const refetch = activeSearch ? refetchSearch : refetchArtifacts

  const allResults: KBQueryResult[] = activeSearch
    ? searchResults?.results ?? []
    : (artifacts ?? [])
        .filter((a) => activeDomains.size === 0 || activeDomains.has(a.domain))
        .map(artifactToResult)

  // Collect available tags from current results for filtering
  const availableTags = useMemo(() => {
    const tagCounts = new Map<string, number>()
    for (const r of allResults) {
      for (const tag of r.tags ?? []) {
        tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1)
      }
    }
    return [...tagCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)
  }, [allResults])

  // Apply tag filter
  const results = activeTag
    ? allResults.filter((r) => r.tags?.includes(activeTag))
    : allResults

  const toggleDomain = useCallback((domain: string) => {
    setActiveDomains((prev) => {
      const next = new Set(prev)
      if (next.has(domain)) next.delete(domain)
      else next.add(domain)
      return next
    })
  }, [])

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
            className="hidden"
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
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") executeSearch()
              if (e.key === "Escape") clearSearch()
            }}
            className="h-9"
          />
          {activeSearch ? (
            <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" onClick={clearSearch}>
              <X className="h-4 w-4" />
            </Button>
          ) : (
            <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" onClick={executeSearch}>
              <Search className="h-4 w-4" />
            </Button>
          )}
        </div>
        <DomainFilter activeDomains={activeDomains} onToggle={toggleDomain} />
        {availableTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <Tag className="h-3 w-3 shrink-0 text-muted-foreground" />
            {availableTags.map(([tag, count]) => (
              <Badge
                key={tag}
                variant={activeTag === tag ? "default" : "secondary"}
                className="cursor-pointer text-[10px]"
                onClick={() => setActiveTag(activeTag === tag ? null : tag)}
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
