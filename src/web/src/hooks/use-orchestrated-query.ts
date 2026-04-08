// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { queryKBOrchestrated } from "@/lib/api"
import { useKBInjection } from "@/contexts/kb-injection-context"
import type {
  ChatMessage,
  KBQueryResult,
  AgentQueryResponse,
  RagMode,
  SourceBreakdown,
  MemoryRecallResult,
  ExternalSourceResult,
} from "@/lib/types"

export interface UseOrchestratedQueryReturn {
  // Query state
  results: KBQueryResult[]
  confidence: number
  totalResults: number
  executionTime: number
  isLoading: boolean
  error: Error | null
  isError: boolean
  refetch: () => void
  hasQueried: boolean

  // Source breakdown (smart/custom_smart modes)
  sourceBreakdown: SourceBreakdown | null
  kbSources: KBQueryResult[]
  memorySources: MemoryRecallResult[]
  externalSources: ExternalSourceResult[]

  // Source toggles (for Knowledge Console)
  kbEnabled: boolean
  memoryEnabled: boolean
  externalEnabled: boolean
  toggleKB: () => void
  toggleMemory: () => void
  toggleExternal: () => void

  // Filter state
  activeDomains: Set<string>
  toggleDomain: (domain: string) => void

  // Manual search
  manualQuery: string
  setManualQuery: (q: string) => void
  executeManualSearch: () => void
  clearManualSearch: () => void

  // Context injection
  injectedContext: KBQueryResult[]
  injectResult: (result: KBQueryResult) => void
  removeInjected: (artifactId: string) => void
  clearInjected: () => void
}

export function useOrchestratedQuery(
  latestUserMessage: string,
  ragMode: RagMode,
  recentMessages?: Pick<ChatMessage, "role" | "content">[],
): UseOrchestratedQueryReturn {
  const [activeDomains, setActiveDomains] = useState<Set<string>>(new Set())
  const [manualQuery, setManualQuery] = useState("")
  const [activeManualQuery, setActiveManualQuery] = useState("")
  const [kbEnabled, setKbEnabled] = useState(true)
  const [memoryEnabled, setMemoryEnabled] = useState(true)
  const [externalEnabled, setExternalEnabled] = useState(true)
  const { injectedContext, injectResult, removeInjected, clearInjected } = useKBInjection()

  const effectiveQuery = activeManualQuery || latestUserMessage

  const domainKey = useMemo(
    () => [...activeDomains].sort().join(","),
    [activeDomains],
  )

  const contextMsgCount = recentMessages?.length ?? 0

  // Guard: treat empty conversation messages as undefined to avoid backend errors
  const conversationMessages =
    recentMessages && recentMessages.length > 0 ? recentMessages : undefined

  const { data, isLoading, isError, error, refetch } = useQuery<AgentQueryResponse>({
    queryKey: ["orchestrated-query", effectiveQuery, ragMode, domainKey, contextMsgCount],
    queryFn: async () => {
      try {
        return await queryKBOrchestrated(
          effectiveQuery,
          ragMode,
          activeDomains.size > 0 ? [...activeDomains] : undefined,
          10,
          conversationMessages,
        )
      } catch {
        // Return a safe empty response so the error state doesn't block the console
        return {
          results: [],
          confidence: 0,
          total_results: 0,
          execution_time_ms: 0,
          source_breakdown: null,
        } as unknown as AgentQueryResponse
      }
    },
    enabled: !!effectiveQuery && effectiveQuery.length > 2,
    staleTime: 15_000,
    retry: 1,
    retryDelay: 2000,
  })

  const toggleDomain = useCallback((domain: string) => {
    setActiveDomains((prev) => {
      const next = new Set(prev)
      if (next.has(domain)) next.delete(domain)
      else next.add(domain)
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

  const toggleKB = useCallback(() => setKbEnabled((v) => !v), [])
  const toggleMemory = useCallback(() => setMemoryEnabled((v) => !v), [])
  const toggleExternal = useCallback(() => setExternalEnabled((v) => !v), [])

  // Parse source breakdown from response
  const sourceBreakdown = data?.source_breakdown ?? null

  const kbSources = useMemo(
    () => (kbEnabled ? sourceBreakdown?.kb ?? [] : []),
    [sourceBreakdown, kbEnabled],
  )
  const memorySources = useMemo(
    () => (memoryEnabled ? sourceBreakdown?.memory ?? [] : []),
    [sourceBreakdown, memoryEnabled],
  )
  const externalSources = useMemo(
    () => (externalEnabled ? sourceBreakdown?.external ?? [] : []),
    [sourceBreakdown, externalEnabled],
  )

  // Filtered results (same relevance threshold as use-kb-context)
  const MIN_RELEVANCE = 0.35
  const filteredResults = useMemo(() => {
    const raw = data?.results ?? []
    if (raw.some((r) => r.relevance > 0)) {
      return raw.filter((r) => r.relevance >= MIN_RELEVANCE)
    }
    return raw
  }, [data])

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

    sourceBreakdown,
    kbSources,
    memorySources,
    externalSources,

    kbEnabled,
    memoryEnabled,
    externalEnabled,
    toggleKB,
    toggleMemory,
    toggleExternal,

    activeDomains,
    toggleDomain,

    manualQuery,
    setManualQuery,
    executeManualSearch,
    clearManualSearch,

    injectedContext,
    injectResult,
    removeInjected,
    clearInjected,
  }
}
