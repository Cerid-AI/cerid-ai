// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useCallback, useEffect } from "react"
import { queryKB } from "@/lib/api"
import type { KBQueryResult } from "@/lib/types"

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
      if (!enabledRef.current || text.length < 10) {
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
        const filtered = result.results
          .filter((r) => !ids.includes(r.artifact_id))
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
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      // Invalidate any in-flight requests
      generationRef.current++
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