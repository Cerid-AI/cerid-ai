// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { createContext, useContext, type ReactNode } from "react"
import { useConversations } from "@/hooks/use-conversations"

type ConversationsContextValue = ReturnType<typeof useConversations>

const ConversationsContext = createContext<ConversationsContextValue | null>(null)

export function ConversationsProvider({ children }: { children: ReactNode }) {
  const value = useConversations()
  return (
    <ConversationsContext value={value}>
      {children}
    </ConversationsContext>
  )
}

export function useConversationsContext(): ConversationsContextValue {
  const ctx = useContext(ConversationsContext)
  if (!ctx) throw new Error("useConversationsContext must be used within ConversationsProvider")
  return ctx
}
