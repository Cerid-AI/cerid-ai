// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

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
  /** True once at least one KB query has returned data. */
  hasQueried: boolean

  // Filter state
  activeDomains: Set<string>
  toggleDomain: (domain: string) => void

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

  const { data, isLoading, error } = useQuery<AgentQueryResponse>({
    queryKey: ["kb-query", effectiveQuery, domainKey, contextMsgCount],
    queryFn: () =>
      queryKB(
        effectiveQuery,
        activeDomains.size > 0 ? [...activeDomains] : undefined,
        10,
        recentMessages,
      ),
    enabled: !!effectiveQuery && effectiveQuery.length > 2,
    staleTime: 15_000,
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

  const executeManualSearch = useCallback(() => {
    if (manualQuery.trim().length > 2) {
      setActiveManualQuery(manualQuery.trim())
    }
  }, [manualQuery])

  const clearManualSearch = useCallback(() => {
    setManualQuery("")
    setActiveManualQuery("")
  }, [])

  return {
    results: data?.results ?? [],
    confidence: data?.confidence ?? 0,
    totalResults: data?.total_results ?? 0,
    executionTime: data?.execution_time_ms ?? 0,
    isLoading,
    error: error ?? null,
    hasQueried: data !== undefined,

    activeDomains,
    toggleDomain,

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