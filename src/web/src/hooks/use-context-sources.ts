// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useCallback } from "react"
import type { ContextSources } from "@/lib/types"

const STORAGE_KEY = "cerid-context-sources"
const DEFAULTS: ContextSources = { kb: true, memory: true, external: true }

function readStored(): ContextSources {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULTS
    return { ...DEFAULTS, ...JSON.parse(raw) }
  } catch {
    return DEFAULTS
  }
}

/**
 * Persisted context-source toggles.
 *
 * Controls which retrieval sources participate in queries (KB, memory,
 * external). State lives in localStorage; the server learns it per request,
 * from the `context_sources` each knowledge query carries. PATCH /settings
 * has no such field, so the sync this used to fire was dropped unread.
 *
 * Conversation context is always included — it cannot be toggled.
 */
export function useContextSources() {
  const [sources, setSources] = useState<ContextSources>(readStored)

  const toggleSource = useCallback((key: keyof ContextSources) => {
    setSources((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const allDisabled = !sources.kb && !sources.memory && !sources.external

  return { sources, toggleSource, allDisabled } as const
}
