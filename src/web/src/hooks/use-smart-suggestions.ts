// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useRef, useCallback, useEffect } from "react"
import { queryKB } from "@/lib/api"
import type { KBQueryResult } from "@/lib/types"

const MIN_SUGGESTION_LENGTH = 10
/** Typeahead must not run a full 20s agent_query — that saturates KB_POOL
 *  while the user is still typing and makes the subsequent send look like an
 *  instant retrieval-budget failure. */
const SUGGESTION_BUDGET_SECONDS = 2
// E1 R3 / CR-010 tail: post-rerank relevance is ordinal — absolute 0.4 emptied
// suggestions on real hits. Gate relative to the top score (same semantics as
// use-chat-send auto-inject). 0.4 means "≥ 40% of the best hit".
const SUGGESTION_RELATIVE_FRACTION = 0.4

interface UseSmartSuggestionsOptions {
  enabled: boolean
  injectedArtifactIds: string[]
  debounceMs?: number
  maxSuggestions?: number
}

export function useSmartSuggestions({
  enabled,
  injectedArtifactIds,
  debounceMs = 1000,
  maxSuggestions = 3,
}: UseSmartSuggestionsOptions) {
  const [suggestions, setSuggestions] = useState<KBQueryResult[]>([])
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastQueryRef = useRef("")
  const abortRef = useRef<AbortController | null>(null)
  // Use refs to avoid stale closures without re-creating the search callback
  const injectedRef = useRef(injectedArtifactIds)
  const enabledRef = useRef(enabled)
  const maxRef = useRef(maxSuggestions)
  // Generation counter to discard stale async responses
  const generationRef = useRef(0)

  useEffect(() => { injectedRef.current = injectedArtifactIds }, [injectedArtifactIds])
  useEffect(() => { enabledRef.current = enabled }, [enabled])
  useEffect(() => { maxRef.current = maxSuggestions }, [maxSuggestions])

  const search = useCallback(
    async (text: string) => {
      if (!enabledRef.current || text.length < MIN_SUGGESTION_LENGTH) {
        setSuggestions([])
        return
      }

      // Avoid duplicate searches
      if (text === lastQueryRef.current) return
      lastQueryRef.current = text

      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac
      const gen = ++generationRef.current
      setLoading(true)
      try {
        const ids = injectedRef.current
        const max = maxRef.current
        const result = await queryKB(text, undefined, max + ids.length, undefined, {
          signal: ac.signal,
          useReranking: false,
          budgetSeconds: SUGGESTION_BUDGET_SECONDS,
        })
        // Discard if a newer search has started
        if (gen !== generationRef.current) return
        const candidates = result.results.filter((r) => !ids.includes(r.artifact_id))
        const topRelevance = candidates.reduce((mx, r) => Math.max(mx, r.relevance), 0)
        const relFloor = topRelevance > 0 ? SUGGESTION_RELATIVE_FRACTION * topRelevance : 0
        const filtered = candidates
          .filter((r) => r.relevance >= relFloor)
          .slice(0, max)
        setSuggestions(filtered)
      } catch {
        // Non-critical — silently fail
      } finally {
        if (gen === generationRef.current) setLoading(false)
      }
    },
    [], // Stable: reads from refs, no closure deps
  )

  const debouncedSearch = useCallback(
    (text: string) => {
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => search(text), debounceMs)
    },
    [search, debounceMs],
  )

  // Cleanup timer on unmount
  useEffect(() => {
    const gen = generationRef
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      abortRef.current?.abort()
      // Invalidate any in-flight requests
      gen.current++
    }
  }, [])

  const dismissSuggestion = useCallback((artifactId: string) => {
    setSuggestions((prev) => prev.filter((s) => s.artifact_id !== artifactId))
  }, [])

  const pinSuggestion = useCallback((artifactId: string) => {
    setPinnedIds((prev) => new Set(prev).add(artifactId))
  }, [])

  const unpinSuggestion = useCallback((artifactId: string) => {
    setPinnedIds((prev) => {
      const next = new Set(prev)
      next.delete(artifactId)
      return next
    })
  }, [])

  const clear = useCallback(() => {
    abortRef.current?.abort()
    setSuggestions([])
    lastQueryRef.current = ""
  }, [])

  return {
    suggestions,
    pinnedIds,
    loading,
    debouncedSearch,
    dismissSuggestion,
    pinSuggestion,
    unpinSuggestion,
    clear,
  }
}