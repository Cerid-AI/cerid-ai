// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { createContext, useContext, useState, useCallback, type ReactNode } from "react"
import type { KBQueryResult } from "@/lib/types"
import { injectionKey } from "@/lib/kb-utils"

interface KBInjectionContextValue {
  injectedContext: KBQueryResult[]
  injectResult: (result: KBQueryResult) => void
  removeInjected: (artifactId: string) => void
  /** Unpin the given chunks, or every pin when called with none. */
  clearInjected: (rows?: KBQueryResult[]) => void
}

const KBInjectionContext = createContext<KBInjectionContextValue | null>(null)

export function KBInjectionProvider({ children }: { children: ReactNode }) {
  const [injectedContext, setInjectedContext] = useState<KBQueryResult[]>([])

  const injectResult = useCallback((result: KBQueryResult) => {
    setInjectedContext((prev) => {
      if (prev.some((r) => r.artifact_id === result.artifact_id && r.chunk_index === result.chunk_index)) {
        return prev
      }
      return [...prev, result]
    })
  }, [])

  const removeInjected = useCallback((artifactId: string) => {
    setInjectedContext((prev) => prev.filter((r) => r.artifact_id !== artifactId))
  }, [])

  const clearInjected = useCallback((rows?: KBQueryResult[]) => {
    if (!rows) {
      setInjectedContext([])
      return
    }
    const gone = new Set(rows.map(injectionKey))
    setInjectedContext((prev) => prev.filter((r) => !gone.has(injectionKey(r))))
  }, [])

  return (
    <KBInjectionContext value={{ injectedContext, injectResult, removeInjected, clearInjected }}>
      {children}
    </KBInjectionContext>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- co-located context hook
export function useKBInjection(): KBInjectionContextValue {
  const ctx = useContext(KBInjectionContext)
  if (!ctx) throw new Error("useKBInjection must be used within KBInjectionProvider")
  return ctx
}