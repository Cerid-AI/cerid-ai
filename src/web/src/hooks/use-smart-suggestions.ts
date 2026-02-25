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

  const search = useCallback(
    async (text: string) => {
      if (!enabled || text.length < 10) {
        setSuggestions([])
        return
      }

      // Avoid duplicate searches
      if (text === lastQueryRef.current) return
      lastQueryRef.current = text

      setLoading(true)
      try {
        const result = await queryKB(text, undefined, maxSuggestions + injectedArtifactIds.length)
        const filtered = result.results
          .filter((r) => !injectedArtifactIds.includes(r.artifact_id))
          .slice(0, maxSuggestions)
        setSuggestions(filtered)
      } catch {
        // Non-critical — silently fail
      } finally {
        setLoading(false)
      }
    },
    [enabled, injectedArtifactIds, maxSuggestions],
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
