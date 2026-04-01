// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useEffect, useMemo } from "react"
import type { Conversation, ChatMessage, HallucinationReport } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { uuid } from "@/lib/utils"
import {
  syncConversation,
  deleteConversationSync,
  fetchSyncedConversations,
} from "@/lib/api"

const STORAGE_KEY = "cerid-conversations"
const MAX_CONVERSATIONS = 50
const SAVE_DEBOUNCE_MS = 500

/** Read private mode flag from localStorage (avoids cross-hook coupling). */
function isPrivateModeActive(): boolean {
  try { return localStorage.getItem("cerid-private-mode") === "true" } catch { return false }
}

const VALID_MODEL_IDS = new Set(MODELS.map((m) => m.id))

/** Migrate old model IDs (missing openrouter/ prefix), validate against current MODELS list,
 *  migrate singular verificationReport → plural verificationReports,
 *  and add archived default. */
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
    // Migrate singular verificationReport → plural verificationReports
    const legacy = c as unknown as Record<string, unknown>
    if (legacy.verificationReport && !c.verificationReports) {
      const lastAssistant = c.messages
        .filter((m) => m.role === "assistant" && m.content).pop()
      if (lastAssistant) {
        c.verificationReports = { [lastAssistant.id]: legacy.verificationReport as HallucinationReport }
      }
      delete legacy.verificationReport
      changed = true
    }
    // Add archived default for pre-existing conversations
    if (c.archived === undefined) {
      c.archived = false
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
  const [activeId, setActiveId] = useState<string | null>(null)

  const active = conversations.find((c) => c.id === activeId) ?? null

  // Verification tracking — persists across ChatPanel unmount/remount (lives in ConversationsContext)
  const [verifiedConversations, setVerifiedConversations] = useState<Set<string>>(() => new Set())

  const markVerified = useCallback((id: string) => {
    setVerifiedConversations((prev) => {
      if (prev.has(id)) return prev
      const next = new Set(prev)
      next.add(id)
      return next
    })
  }, [])

  const clearVerified = useCallback((id: string) => {
    setVerifiedConversations((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }, [])

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

  // Cloud sync: fire-and-forget server persistence (debounced for streaming)
  const serverSyncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingServerSyncRef = useRef<Conversation | null>(null)

  const syncToServer = useCallback((convo: Conversation) => {
    // Skip server sync in private mode — conversations stay local only
    if (isPrivateModeActive()) return
    pendingServerSyncRef.current = convo
    if (!serverSyncTimerRef.current) {
      serverSyncTimerRef.current = setTimeout(() => {
        serverSyncTimerRef.current = null
        const pending = pendingServerSyncRef.current
        if (pending) {
          pendingServerSyncRef.current = null
          syncConversation(pending).catch(() => { /* fire-and-forget */ })
        }
      }, 2000)
    }
  }, [])

  // Flush any pending save on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      if (pendingRef.current) saveConversations(pendingRef.current)
      if (serverSyncTimerRef.current) clearTimeout(serverSyncTimerRef.current)
      if (pendingServerSyncRef.current) {
        syncConversation(pendingServerSyncRef.current).catch(() => {})
      }
    }
  }, [])

  const create = useCallback((model: string) => {
    const convo: Conversation = {
      id: uuid(),
      title: "New conversation",
      messages: [],
      model,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    setConversations((prev) => {
      const next = [convo, ...prev]
      saveConversations(next)
      if (!isPrivateModeActive()) {
        syncConversation(convo).catch(() => { /* fire-and-forget */ })
      }
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
      const updated = next.find((c) => c.id === convoId)
      if (updated) syncToServer(updated)
      return next
    })
  }, [syncToServer])

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
      const updated = next.find((c) => c.id === convoId)
      if (updated) syncToServer(updated)
      return next
    })
  }, [debouncedSave, syncToServer])

  const updateLastMessageModel = useCallback((convoId: string, model: string) => {
    setConversations((prev) => {
      const next = prev.map((c) => {
        if (c.id !== convoId) return c
        const messages = [...c.messages]
        const last = messages[messages.length - 1]
        if (last?.role === "assistant") {
          messages[messages.length - 1] = { ...last, model }
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
      deleteConversationSync(convoId).catch(() => { /* fire-and-forget */ })
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

  /** Persist a verification report for a specific message in localStorage. */
  const saveVerification = useCallback((convoId: string, msgId: string, report: HallucinationReport | null) => {
    setConversations((prev) => {
      const next = prev.map((c) => {
        if (c.id !== convoId) return c
        const reports = { ...(c.verificationReports ?? {}) }
        if (report) {
          reports[msgId] = report
        } else {
          delete reports[msgId]
        }
        return { ...c, verificationReports: reports, updatedAt: Date.now() }
      })
      saveConversations(next)
      const updated = next.find((c) => c.id === convoId)
      if (updated) syncToServer(updated)
      return next
    })
  }, [syncToServer])

  /** Get the stored verification report for a specific message. */
  const getVerification = useCallback((convoId: string, msgId: string): HallucinationReport | null => {
    const convo = conversations.find((c) => c.id === convoId)
    return convo?.verificationReports?.[msgId] ?? null
  }, [conversations])

  /** Get all stored verification reports for a conversation (keyed by message ID). */
  const getAllVerificationReports = useCallback((convoId: string): Record<string, HallucinationReport> => {
    const convo = conversations.find((c) => c.id === convoId)
    return convo?.verificationReports ?? {}
  }, [conversations])

  // Archive / unarchive
  const [showArchived, setShowArchived] = useState(false)

  const toggleShowArchived = useCallback(() => {
    setShowArchived((prev) => !prev)
  }, [])

  const archive = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, archived: true, updatedAt: Date.now() } : c
      )
      saveConversations(next)
      setActiveId((currentId) => {
        if (currentId !== convoId) return currentId
        const firstActive = next.find((c) => !c.archived)
        return firstActive?.id ?? null
      })
      return next
    })
  }, [])

  const unarchive = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, archived: false, updatedAt: Date.now() } : c
      )
      saveConversations(next)
      return next
    })
  }, [])

  const archivedCount = useMemo(
    () => conversations.filter((c) => c.archived).length,
    [conversations],
  )

  const visibleConversations = useMemo(
    () => showArchived
      ? conversations.filter((c) => c.archived)
      : conversations.filter((c) => !c.archived),
    [conversations, showArchived],
  )

  // Bulk operations
  const bulkDelete = useCallback((ids: string[]) => {
    if (ids.length === 0) return
    const idSet = new Set(ids)
    setConversations((prev) => {
      const next = prev.filter((c) => !idSet.has(c.id))
      saveConversations(next)
      for (const id of ids) {
        deleteConversationSync(id).catch(() => {})
      }
      setActiveId((currentId) => {
        if (!currentId || !idSet.has(currentId)) return currentId
        return next[0]?.id ?? null
      })
      return next
    })
  }, [])

  const bulkArchive = useCallback((ids: string[]) => {
    if (ids.length === 0) return
    const idSet = new Set(ids)
    setConversations((prev) => {
      const next = prev.map((c) =>
        idSet.has(c.id) ? { ...c, archived: true, updatedAt: Date.now() } : c
      )
      saveConversations(next)
      setActiveId((currentId) => {
        if (!currentId || !idSet.has(currentId)) return currentId
        const firstActive = next.find((c) => !c.archived)
        return firstActive?.id ?? null
      })
      return next
    })
  }, [])

  // Hydrate from server on mount — merge server conversations with localStorage
  const serverHydratedRef = useRef(false)
  useEffect(() => {
    if (serverHydratedRef.current) return
    serverHydratedRef.current = true

    fetchSyncedConversations()
      .then((serverConvos) => {
        if (!serverConvos.length) return
        setConversations((local) => {
          const localIds = new Set(local.map((c) => c.id))
          const newFromServer = serverConvos.filter((sc) => !localIds.has(sc.id))
          if (newFromServer.length === 0) return local

          const merged = [...local, ...newFromServer].slice(0, MAX_CONVERSATIONS)
          saveConversations(merged)
          return merged
        })
      })
      .catch(() => { /* Server unavailable */ })
  }, [])

  return {
    conversations, visibleConversations, active, activeId, setActiveId,
    create, addMessage, updateLastMessage, updateLastMessageModel, updateModel, remove,
    replaceMessages, clearMessages,
    verifiedConversations, markVerified, clearVerified,
    saveVerification, getVerification, getAllVerificationReports,
    archive, unarchive, showArchived, toggleShowArchived, archivedCount,
    bulkDelete, bulkArchive,
  }
}