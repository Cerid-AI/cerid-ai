// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useEffect } from "react"
import type { Conversation, ChatMessage } from "@/lib/types"
import { MODELS } from "@/lib/types"

const STORAGE_KEY = "cerid-conversations"
const MAX_CONVERSATIONS = 50
const SAVE_DEBOUNCE_MS = 500

const VALID_MODEL_IDS = new Set(MODELS.map((m) => m.id))

/** Migrate old model IDs (missing openrouter/ prefix) and validate against current MODELS list */
function migrateConversations(convos: Conversation[]): Conversation[] {
  let changed = false
  const migrated = convos.map((c) => {
    let model = c.model
    if (model && !model.startsWith("openrouter/")) {
      model = `openrouter/${model}`
      changed = true
    }
    if (model && !VALID_MODEL_IDS.has(model)) {
      model = MODELS[0].id
      changed = true
    }
    return model !== c.model ? { ...c, model } : c
  })
  if (changed) saveConversations(migrated)
  return migrated
}

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const convos: Conversation[] = raw ? JSON.parse(raw) : []
    return migrateConversations(convos)
  } catch {
    return []
  }
}

function saveConversations(convos: Conversation[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(convos.slice(0, MAX_CONVERSATIONS)))
  } catch {
    // localStorage may be full or unavailable
  }
}

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState<string | null>(() => conversations[0]?.id ?? null)

  const active = conversations.find((c) => c.id === activeId) ?? null

  // Debounced save: flushes to localStorage at most every SAVE_DEBOUNCE_MS.
  // Immediate saves (create, delete, model change) call saveConversations directly.
  // High-frequency updates (streaming chunks) use debouncedSave.
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingRef = useRef<Conversation[] | null>(null)

  const debouncedSave = useCallback((convos: Conversation[]) => {
    pendingRef.current = convos
    if (!saveTimerRef.current) {
      saveTimerRef.current = setTimeout(() => {
        saveTimerRef.current = null
        if (pendingRef.current) {
          saveConversations(pendingRef.current)
          pendingRef.current = null
        }
      }, SAVE_DEBOUNCE_MS)
    }
  }, [])

  // Flush any pending save on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      if (pendingRef.current) saveConversations(pendingRef.current)
    }
  }, [])

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
      debouncedSave(next)
      return next
    })
  }, [debouncedSave])

  const updateModel = useCallback((convoId: string, model: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, model, updatedAt: Date.now() } : c
      )
      saveConversations(next)
      return next
    })
  }, [])

  const remove = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== convoId)
      saveConversations(next)
      // Derive next active ID from fresh state (avoids stale closure)
      setActiveId((currentId) => {
        if (currentId !== convoId) return currentId
        return next[0]?.id ?? null
      })
      return next
    })
  }, [])

  const replaceMessages = useCallback((convoId: string, newMessages: ChatMessage[]) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, messages: newMessages, updatedAt: Date.now() } : c,
      )
      saveConversations(next)
      return next
    })
  }, [])

  const clearMessages = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, messages: [], updatedAt: Date.now() } : c,
      )
      saveConversations(next)
      return next
    })
  }, [])

  return {
    conversations, active, activeId, setActiveId,
    create, addMessage, updateLastMessage, updateModel, remove,
    replaceMessages, clearMessages,
  }
}