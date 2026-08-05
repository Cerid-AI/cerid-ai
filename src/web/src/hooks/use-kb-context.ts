// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useCallback, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { queryKB } from "@/lib/api"
import { useKBInjection } from "@/contexts/kb-injection-context"
import type { ChatMessage, KBQueryResult, AgentQueryResponse } from "@/lib/types"

export interface UseKBContextReturn {
  // Query state
  results: KBQueryResult[]
  confidence: number
  totalResults: number
  executionTime: number
  isLoading: boolean
  error: Error | null
  isError: boolean
  refetch: () => void
  /** True once at least one KB query has returned data. */
  hasQueried: boolean

  // Filter state
  activeDomains: Set<string>
  toggleDomain: (domain: string) => void
  activeTags: string[]
  toggleTag: (tag: string) => void

  // Manual search
  manualQuery: string
  setManualQuery: (q: string) => void
  executeManualSearch: () => void
  clearManualSearch: () => void

  // Selection
  selectedArtifactId: string | null
  setSelectedArtifactId: (id: string | null) => void

  // Context injection (shared via KBInjectionContext)
  injectedContext: KBQueryResult[]
  injectResult: (result: KBQueryResult) => void
  removeInjected: (artifactId: string) => void
  clearInjected: () => void
}

export function useKBContext(
  latestUserMessage: string,
  recentMessages?: Pick<ChatMessage, "role" | "content">[],
): UseKBContextReturn {
  const [activeDomains, setActiveDomains] = useState<Set<string>>(new Set())
  const [activeTags, setActiveTags] = useState<string[]>([])
  const [manualQuery, setManualQuery] = useState("")
  const [activeManualQuery, setActiveManualQuery] = useState("")
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null)
  const { injectedContext, injectResult, removeInjected, clearInjected } = useKBInjection()

  // The effective query: manual overrides auto
  const effectiveQuery = activeManualQuery || latestUserMessage

  const domainKey = useMemo(
    () => [...activeDomains].sort().join(","),
    [activeDomains],
  )

  // Stable key for conversation context (re-query when message count changes)
  const contextMsgCount = recentMessages?.length ?? 0

  const { data, isLoading, isError, error, refetch } = useQuery<AgentQueryResponse>({
    queryKey: ["kb-query", effectiveQuery, domainKey, contextMsgCount],
    queryFn: ({ signal }) =>
      queryKB(
        effectiveQuery,
        activeDomains.size > 0 ? [...activeDomains] : undefined,
        10,
        recentMessages,
        { signal },
      ),
    enabled: !!effectiveQuery && effectiveQuery.length > 2,
    staleTime: 15_000,
    retry: 1,
  })

  const toggleDomain = useCallback((domain: string) => {
    setActiveDomains((prev) => {
      const next = new Set(prev)
      if (next.has(domain)) {
        next.delete(domain)
      } else {
        next.add(domain)
      }
      return next
    })
  }, [])

  const toggleTag = useCallback((tag: string) => {
    setActiveTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    )
  }, [])

  const executeManualSearch = useCallback(() => {
    if (manualQuery.trim().length > 2) {
      setActiveManualQuery(manualQuery.trim())
    }
  }, [manualQuery])

  const clearManualSearch = useCallback(() => {
    setManualQuery("")
    setActiveManualQuery("")
  }, [])

  // Tag filtering only. The backend applies its calibrated relevance floor
  // pre-rerank (query_agent Step 4.95) and returns a ranked, top-k set; the
  // post-rerank `relevance` field is an ordinal cross-encoder sigmoid, so a
  // client-side absolute floor here re-created the emptied-envelope bug the
  // backend already fixed server-side — dropping correct chunks that scored
  // ~0.28 on the ordinal scale (CR-010).
  const filteredResults = useMemo(() => {
    const results = data?.results ?? []
    if (activeTags.length === 0) return results
    return results.filter((r) => {
      const rTags = r.tags ?? []
      return activeTags.every((t) => rTags.includes(t))
    })
  }, [data?.results, activeTags])

  return {
    results: filteredResults,
    confidence: data?.confidence ?? 0,
    totalResults: data?.total_results ?? 0,
    executionTime: data?.execution_time_ms ?? 0,
    isLoading,
    error: error ?? null,
    isError,
    refetch,
    hasQueried: data !== undefined,

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
    removeInjected,
    clearInjected,
  }
}