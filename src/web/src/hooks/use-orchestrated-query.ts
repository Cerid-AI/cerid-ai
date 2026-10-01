// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useCallback, useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { queryKBOrchestrated } from "@/lib/api"
import { useKBInjection } from "@/contexts/kb-injection-context"
import type {
  ChatMessage,
  ContextSources,
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

  /** Non-empty string when the retrieval pipeline exceeded its time budget
   *  for the most recent query and returned an ungrounded answer. */
  degradedReason: string

  /** Retrieve for a message about to be sent, sharing the panel's request. */
  retrieveFor: (
    query: string,
    recentAfterSend: Pick<ChatMessage, "role" | "content">[],
  ) => Promise<AgentQueryResponse>

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

interface OrchestratedQueryInput {
  query: string
  ragMode: RagMode
  domains: Set<string>
  recentMessages?: Pick<ChatMessage, "role" | "content">[]
  contextSources?: ContextSources
}

/** The key and the request for one orchestrated query. The panel's useQuery and
 *  the send's retrieval both build theirs here, so the two are one cache entry
 *  and one request: issued separately they ran side by side, and three
 *  concurrent retrievals took 16 s where one takes 4. */
function orchestratedQueryOptions(input: OrchestratedQueryInput) {
  const { query, ragMode, domains, contextSources } = input
  const recent =
    input.recentMessages && input.recentMessages.length > 0 ? input.recentMessages : undefined
  const domainKey = [...domains].sort().join(",")
  const sourcesKey = `${contextSources?.kb ?? true},${contextSources?.memory ?? true},${contextSources?.external ?? true}`
  return {
    queryKey: ["orchestrated-query", query, ragMode, domainKey, recent?.length ?? 0, sourcesKey],
    queryFn: ({ signal }: { signal?: AbortSignal }) =>
      queryKBOrchestrated(
        query,
        ragMode,
        domains.size > 0 ? [...domains] : undefined,
        10,
        recent,
        undefined,
        contextSources,
        { signal },
      ),
    staleTime: 15_000,
  }
}

export function useOrchestratedQuery(
  latestUserMessage: string,
  ragMode: RagMode,
  recentMessages?: Pick<ChatMessage, "role" | "content">[],
  contextSources?: ContextSources,
  opts?: { enabled?: boolean },
): UseOrchestratedQueryReturn {
  const [activeDomains, setActiveDomains] = useState<Set<string>>(new Set())
  const [manualQuery, setManualQuery] = useState("")
  const [activeManualQuery, setActiveManualQuery] = useState("")
  const { injectedContext, injectResult, removeInjected, clearInjected } = useKBInjection()
  const autoEnabled = opts?.enabled ?? true

  // Source gates driven by parent (useContextSources hook) — not local state
  const kbEnabled = contextSources?.kb ?? true
  const memoryEnabled = contextSources?.memory ?? true
  const externalEnabled = contextSources?.external ?? true

  const effectiveQuery = activeManualQuery || latestUserMessage
  const queryClient = useQueryClient()

  const { data, isLoading, isError, error, refetch } = useQuery<AgentQueryResponse>({
    // Let failures propagate to react-query so `isError` drives the
    // console's error + Retry state. Swallowing here (returning an empty
    // response) made that UI permanently dead — a backend outage read as
    // "no results". TanStack treats signal-abort as a cancellation, not an
    // error, so query-key churn won't flash the error state.
    ...orchestratedQueryOptions({
      query: effectiveQuery,
      ragMode,
      domains: activeDomains,
      recentMessages,
      contextSources,
    }),
    // `enabled` gates only the auto latestUserMessage query. Manual search
    // (activeManualQuery) still fires when auto is suppressed.
    enabled:
      (autoEnabled && !!effectiveQuery && effectiveQuery.length > 2) ||
      (!!activeManualQuery && activeManualQuery.length > 2),
    retry: 1,
    retryDelay: 2000,
  })

  // The send's retrieval for a message that is about to join the conversation.
  // `recentAfterSend` is what `recentMessages` will be once it has, so the key
  // is the one this hook asks for a render later and the request is shared.
  const retrieveFor = useCallback(
    (query: string, recentAfterSend: Pick<ChatMessage, "role" | "content">[]) =>
      queryClient.fetchQuery<AgentQueryResponse>(
        orchestratedQueryOptions({
          query,
          ragMode,
          domains: activeDomains,
          recentMessages: recentAfterSend,
          contextSources,
        }),
      ),
    [queryClient, ragMode, activeDomains, contextSources],
  )

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

  // Toggle callbacks are no-ops here — context sources are controlled by the
  // parent via useContextSources().  The return interface keeps them for
  // KnowledgeConsole compatibility; chat-panel wires the real callbacks.
  const toggleKB = useCallback(() => {}, [])
  const toggleMemory = useCallback(() => {}, [])
  const toggleExternal = useCallback(() => {}, [])

  // Parse source breakdown from response
  const sourceBreakdown = data?.source_breakdown ?? null
  const degradedReason = data?.degraded_reason ?? ""

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

  // No client-side absolute floor: the backend floors on its calibrated
  // retrieval scale pre-rerank and returns a ranked set, then rerank replaces
  // `relevance` with an ordinal cross-encoder sigmoid. An absolute FE threshold
  // on that ordinal score re-created the emptied-envelope bug (CR-010).
  const filteredResults = useMemo(() => data?.results ?? [], [data])

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

    degradedReason,
    retrieveFor,

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
