import { createContext, useContext, useState, useCallback, type ReactNode } from "react"
import type { KBQueryResult } from "@/lib/types"

interface KBInjectionContextValue {
  injectedContext: KBQueryResult[]
  injectResult: (result: KBQueryResult) => void
  removeInjected: (artifactId: string) => void
  clearInjected: () => void
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

  const clearInjected = useCallback(() => {
    setInjectedContext([])
  }, [])

  return (
    <KBInjectionContext value={{ injectedContext, injectResult, removeInjected, clearInjected }}>
      {children}
    </KBInjectionContext>
  )
}

export function useKBInjection(): KBInjectionContextValue {
  const ctx = useContext(KBInjectionContext)
  if (!ctx) throw new Error("useKBInjection must be used within KBInjectionProvider")
  return ctx
}
