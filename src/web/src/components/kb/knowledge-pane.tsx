import { useState, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Search, X, Loader2 } from "lucide-react"
import { ArtifactCard } from "./artifact-card"
import { DomainFilter } from "./domain-filter"
import { GraphPreview } from "./graph-preview"
import { fetchArtifacts, queryKB } from "@/lib/api"
import { useKBInjection } from "@/contexts/kb-injection-context"
import type { KBQueryResult, Artifact } from "@/lib/types"

function artifactToResult(a: Artifact): KBQueryResult {
  return {
    content: a.summary || "",
    relevance: 0,
    artifact_id: a.id,
    filename: a.filename,
    domain: a.domain,
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

  const domainKey = [...activeDomains].sort().join(",")

  // Browse mode: list recent artifacts
  const { data: artifacts, isLoading: browsing } = useQuery({
    queryKey: ["artifacts", domainKey],
    queryFn: () => {
      const domain = activeDomains.size === 1 ? [...activeDomains][0] : undefined
      return fetchArtifacts(domain, 200)
    },
    enabled: !activeSearch,
    staleTime: 30_000,
  })

  // Search mode: query KB
  const { data: searchResults, isLoading: searching } = useQuery({
    queryKey: ["kb-search", activeSearch, [...activeDomains].sort().join(",")],
    queryFn: () =>
      queryKB(activeSearch, activeDomains.size > 0 ? [...activeDomains] : undefined),
    enabled: !!activeSearch && activeSearch.length > 2,
    staleTime: 60_000,
  })

  const isLoading = activeSearch ? searching : browsing

  const results: KBQueryResult[] = activeSearch
    ? searchResults?.results ?? []
    : (artifacts ?? [])
        .filter((a) => activeDomains.size === 0 || activeDomains.has(a.domain))
        .map(artifactToResult)

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
        <h2 className="text-lg font-semibold">Knowledge Base</h2>
        <p className="text-xs text-muted-foreground">
          {activeSearch
            ? `${results.length} results for "${activeSearch}"`
            : `${results.length} artifacts`}
        </p>
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

          {!isLoading && results.length === 0 && (
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
