import { useState, useCallback } from "react"
import type { Conversation, ChatMessage } from "@/lib/types"

const STORAGE_KEY = "cerid-conversations"
const MAX_CONVERSATIONS = 50

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveConversations(convos: Conversation[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(convos.slice(0, MAX_CONVERSATIONS)))
}

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState<string | null>(() => conversations[0]?.id ?? null)

  const active = conversations.find((c) => c.id === activeId) ?? null

  const create = useCallback((model: string) => {
    const convo: Conversation = {
      id: crypto.randomUUID(),
      title: "New conversation",
      messages: [],
      model,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    setConversations((prev) => {
      const next = [convo, ...prev]
      saveConversations(next)
      return next
    })
    setActiveId(convo.id)
    return convo.id
  }, [])

  const addMessage = useCallback((convoId: string, message: ChatMessage) => {
    setConversations((prev) => {
      const next = prev.map((c) => {
        if (c.id !== convoId) return c
        const messages = [...c.messages, message]
        const title = c.messages.length === 0 && message.role === "user"
          ? message.content.slice(0, 60) + (message.content.length > 60 ? "..." : "")
          : c.title
        return { ...c, messages, title, updatedAt: Date.now() }
      })
      saveConversations(next)
      return next
    })
  }, [])

  const updateLastMessage = useCallback((convoId: string, content: string) => {
    setConversations((prev) => {
      const next = prev.map((c) => {
        if (c.id !== convoId) return c
        const messages = [...c.messages]
        if (messages.length > 0) {
          messages[messages.length - 1] = { ...messages[messages.length - 1], content }
        }
        return { ...c, messages, updatedAt: Date.now() }
      })
      saveConversations(next)
      return next
    })
  }, [])

  const remove = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== convoId)
      saveConversations(next)
      return next
    })
    setActiveId((prev) => {
      if (prev !== convoId) return prev
      const remaining = conversations.filter((c) => c.id !== convoId)
      return remaining[0]?.id ?? null
    })
  }, [conversations])

  return { conversations, active, activeId, setActiveId, create, addMessage, updateLastMessage, remove }
}
