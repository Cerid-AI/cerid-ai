// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { createContext, useContext, useMemo, type ReactNode } from "react"
import { useConversations } from "@/hooks/use-conversations"

type ConversationsContextValue = ReturnType<typeof useConversations>

const ConversationsContext = createContext<ConversationsContextValue | null>(null)

export function ConversationsProvider({ children }: { children: ReactNode }) {
  const value = useConversations()
  // Memoize context value to stabilize the object reference.
  // Functions from useConversations are already stable (useCallback).
  // Only re-create when data values change, not on every render.
  const memoized = useMemo(
    () => value,
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable callbacks omitted intentionally
    [value.conversations, value.visibleConversations, value.active, value.activeId, value.verifiedConversations, value.showArchived, value.archivedCount],
  )
  return (
    <ConversationsContext value={memoized}>
      {children}
    </ConversationsContext>
  )
}

export function useConversationsContext(): ConversationsContextValue {
  const ctx = useContext(ConversationsContext)
  if (!ctx) throw new Error("useConversationsContext must be used within ConversationsProvider")
  return ctx
}
