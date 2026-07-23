// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useCallback, useEffect } from "react"
import { queryKB } from "@/lib/api"
import type { KBQueryResult } from "@/lib/types"

const MIN_SUGGESTION_LENGTH = 10
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
  debounceMs = 500,
  maxSuggestions = 3,
}: UseSmartSuggestionsOptions) {
  const [suggestions, setSuggestions] = useState<KBQueryResult[]>([])
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastQueryRef = useRef("")
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

      const gen = ++generationRef.current
      setLoading(true)
      try {
        const ids = injectedRef.current
        const max = maxRef.current
        const result = await queryKB(text, undefined, max + ids.length)
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