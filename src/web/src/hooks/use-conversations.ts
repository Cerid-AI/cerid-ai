// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useCallback, useRef, useEffect, useMemo } from "react"
import type { Conversation, ChatMessage, HallucinationReport } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { uuid } from "@/lib/utils"
import {
  syncConversation,
  deleteConversationSync,
  fetchSyncedConversations,
  fetchForgottenConversations,
  ConversationGoneError,
} from "@/lib/api"

const STORAGE_KEY = "cerid-conversations"
// Delete tombstones: ids whose local delete hasn't been acked by the server.
// Persisted so a failed/remote-replica delete can't resurrect on mount (CR-092).
const TOMBSTONE_KEY = "cerid-conversation-tombstones"
// Cursor into the server's forgotten feed (GET /user-state/forgotten?since=).
// The server registry is the authority for every forget it has acknowledged;
// the tombstones above are only an outbox for deletes it has not yet seen.
const FORGOTTEN_CURSOR_KEY = "cerid-forgotten-cursor"
const MAX_CONVERSATIONS = 50
const LOCAL_DEBOUNCE_MS = 500
const SERVER_DEBOUNCE_MS = 2000

/** Read private mode flag from localStorage (avoids cross-hook coupling). */
function isPrivateModeActive(): boolean {
  try { return localStorage.getItem("cerid-private-mode") === "true" } catch { return false }
}

/** Mark a conversation private once it takes message content while private
 *  mode is on. The mark is never cleared: turning private mode off later must
 *  not let what was said under it reach storage or the server. */
function markPrivate(c: Conversation): Conversation {
  return !c.private && isPrivateModeActive() ? { ...c, private: true } : c
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
    const stored: Conversation[] = raw ? JSON.parse(raw) : []
    const convos = stored.filter((c) => !c.private)
    if (convos.length !== stored.length) saveConversations(convos)
    return migrateConversations(convos)
  } catch {
    return []
  }
}

function saveConversations(convos: Conversation[]) {
  try {
    const storable = convos.filter((c) => !c.private)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(storable.slice(0, MAX_CONVERSATIONS)))
  } catch {
    // localStorage may be full or unavailable
  }
}

function loadTombstones(): Set<string> {
  try {
    const raw = localStorage.getItem(TOMBSTONE_KEY)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

function saveTombstones(ids: Set<string>) {
  try {
    localStorage.setItem(TOMBSTONE_KEY, JSON.stringify([...ids]))
  } catch {
    // localStorage may be full or unavailable
  }
}

/** A queued server mutation for one conversation id. */
type ServerOp = { kind: "upsert"; convo: Conversation } | { kind: "delete" }

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState<string | null>(null)

  const active = conversations.find((c) => c.id === activeId) ?? null

  // Set when a server sync op fails and cleared on the next success — a
  // persistent "not syncing" signal instead of the previous fire-and-forget
  // catches that swallowed failures with no visible trace (WB-46). The mount
  // merge below re-pushes on every reload, so without this a failing sync
  // just retried silently forever with no way to tell.
  const [syncFailing, setSyncFailing] = useState(false)

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

  // ── Persistence coordinator ─────────────────────────────────────────────
  // Single owner of localStorage + server sync for every mutator. Each mutator
  // reduces to a pure state update plus `persist(next, …)`: one localStorage
  // channel and a per-id server queue. This replaces the old scatter where
  // some mutators synced and others didn't (CR-060/CR-110), a single-slot
  // server debounce dropped syncs (CR-083), a stale debounced snapshot could
  // clobber an immediate write (CR-101), and deletes left no tombstone (CR-092).

  // Delete tombstones — persisted; consulted by the mount merge so a deleted
  // conversation can't be re-added from a server replica (CR-092).
  const tombstonesRef = useRef<Set<string>>(loadTombstones())

  const addTombstone = useCallback((id: string) => {
    if (!tombstonesRef.current.has(id)) {
      tombstonesRef.current.add(id)
      saveTombstones(tombstonesRef.current)
    }
  }, [])

  const clearTombstone = useCallback((id: string) => {
    if (tombstonesRef.current.delete(id)) {
      saveTombstones(tombstonesRef.current)
    }
  }, [])

  // localStorage channel — one debounce timer. `flushLocalNow` cancels the
  // pending timer before writing, so a stale streaming snapshot can never fire
  // after (and revert) a delete/archive (CR-101). The timer always persists the
  // freshest snapshot handed to `scheduleLocal`.
  const localTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const localPendingRef = useRef<Conversation[] | null>(null)

  const flushLocalNow = useCallback((convos: Conversation[]) => {
    if (localTimerRef.current) {
      clearTimeout(localTimerRef.current)
      localTimerRef.current = null
    }
    localPendingRef.current = null
    saveConversations(convos)
  }, [])

  const scheduleLocal = useCallback((convos: Conversation[]) => {
    localPendingRef.current = convos
    if (!localTimerRef.current) {
      localTimerRef.current = setTimeout(() => {
        localTimerRef.current = null
        if (localPendingRef.current) {
          saveConversations(localPendingRef.current)
          localPendingRef.current = null
        }
      }, LOCAL_DEBOUNCE_MS)
    }
  }, [])

  // Drop conversations the server has forgotten (from the feed, or a 410 on a
  // save). The server acked the forget, so any local tombstone is done too.
  const dropLocal = useCallback((ids: Set<string>) => {
    if (ids.size === 0) return
    setConversations((prev) => {
      const next = prev.filter((c) => !ids.has(c.id))
      if (next.length === prev.length) return prev
      flushLocalNow(next)
      return next
    })
    setActiveId((currentId) => (currentId && ids.has(currentId) ? null : currentId))
    for (const id of ids) clearTombstone(id)
  }, [flushLocalNow, clearTombstone])

  const pushConversation = useCallback((convo: Conversation) => {
    syncConversation(convo)
      .then(() => setSyncFailing(false))
      .catch((err) => {
        if (err instanceof ConversationGoneError) dropLocal(new Set([err.id]))
        else setSyncFailing(true)  // mount merge re-pushes
      })
  }, [dropLocal])

  // Server channel — per-id debounced flush (a Map, not one shared slot), so a
  // second conversation syncing inside the window can't drop the first's
  // pending sync (CR-083). Private mode is resolved at flush time and applies
  // symmetrically to upserts AND deletes — private means local-only, so neither
  // leaves the browser (CR-061). Local tombstones still suppress resurrection.
  const serverTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const serverPendingRef = useRef<Map<string, ServerOp>>(new Map())

  const flushServer = useCallback((id: string) => {
    const timer = serverTimersRef.current.get(id)
    if (timer) {
      clearTimeout(timer)
      serverTimersRef.current.delete(id)
    }
    const op = serverPendingRef.current.get(id)
    serverPendingRef.current.delete(id)
    if (!op) return
    if (isPrivateModeActive()) return  // local-only; symmetric for upsert + delete
    if (op.kind === "upsert" && op.convo.private) return
    if (op.kind === "delete") {
      deleteConversationSync(id)
        .then(() => { clearTombstone(id); setSyncFailing(false) })  // server acked → forget the tombstone
        .catch(() => setSyncFailing(true))       // keep tombstone; retried by the mount merge
    } else {
      pushConversation(op.convo)
    }
  }, [clearTombstone, pushConversation])

  const enqueueServer = useCallback((id: string, op: ServerOp, immediate = false) => {
    serverPendingRef.current.set(id, op)  // latest op per id wins
    if (immediate) {
      flushServer(id)
      return
    }
    if (!serverTimersRef.current.has(id)) {
      serverTimersRef.current.set(id, setTimeout(() => flushServer(id), SERVER_DEBOUNCE_MS))
    }
  }, [flushServer])

  // Persist a post-mutation state: localStorage (immediate for structural
  // changes, debounced for high-frequency streaming) + an optional server op.
  const persist = useCallback((
    next: Conversation[],
    opts: { convoId?: string; server?: ServerOp; streaming?: boolean; immediateServer?: boolean } = {},
  ) => {
    if (opts.streaming) scheduleLocal(next)
    else flushLocalNow(next)
    if (opts.convoId && opts.server) enqueueServer(opts.convoId, opts.server, opts.immediateServer)
  }, [scheduleLocal, flushLocalNow, enqueueServer])

  // Flush any pending local write + drain queued server ops on unmount. The
  // timer/queue Maps are created once and never reassigned, so capturing them
  // at setup is identical to reading the refs in cleanup.
  useEffect(() => {
    const serverTimers = serverTimersRef.current
    const serverPending = serverPendingRef.current
    return () => {
      if (localTimerRef.current) clearTimeout(localTimerRef.current)
      if (localPendingRef.current) saveConversations(localPendingRef.current)
      for (const timer of serverTimers.values()) clearTimeout(timer)
      if (!isPrivateModeActive()) {
        for (const [id, op] of serverPending) {
          if (op.kind === "delete") {
            deleteConversationSync(id).then(() => clearTombstone(id)).catch(() => {})
          } else if (!op.convo.private) {
            syncConversation(op.convo).catch(() => {})
          }
        }
      }
      serverTimers.clear()
      serverPending.clear()
    }
  }, [clearTombstone])

  const create = useCallback((model: string) => {
    const convo: Conversation = {
      id: uuid(),
      title: "New conversation",
      messages: [],
      model,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    // A brand-new id can't be a deleted one; drop any stale tombstone.
    clearTombstone(convo.id)
    setConversations((prev) => {
      const next = [convo, ...prev]
      flushLocalNow(next)
      return next
    })
    setActiveId(convo.id)
    enqueueServer(convo.id, { kind: "upsert", convo }, true)
    return convo.id
  }, [clearTombstone, flushLocalNow, enqueueServer])

  const addMessage = useCallback((convoId: string, message: ChatMessage) => {
    setConversations((prev) => {
      const next = prev.map((c) => {
        if (c.id !== convoId) return c
        const messages = [...c.messages, message]
        const title = c.messages.length === 0 && message.role === "user"
          ? message.content.slice(0, 60) + (message.content.length > 60 ? "..." : "")
          : c.title
        return markPrivate({ ...c, messages, title, updatedAt: Date.now() })
      })
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      return next
    })
  }, [persist])

  const updateLastMessage = useCallback((convoId: string, content: string) => {
    setConversations((prev) => {
      const next = prev.map((c) => {
        if (c.id !== convoId) return c
        const messages = [...c.messages]
        if (messages.length > 0) {
          messages[messages.length - 1] = { ...messages[messages.length - 1], content }
        }
        return markPrivate({ ...c, messages, updatedAt: Date.now() })
      })
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined, streaming: true })
      return next
    })
  }, [persist])

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
      // Route through the server too — model-fallback attribution on an aborted
      // stream was previously localStorage-only and never reached the server (CR-110).
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined, streaming: true })
      return next
    })
  }, [persist])

  const updateModel = useCallback((convoId: string, model: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, model, updatedAt: Date.now() } : c
      )
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      return next
    })
  }, [persist])

  const remove = useCallback((convoId: string) => {
    // Tombstone before the delete so a failed/remote-replica DELETE can't
    // resurrect this conversation on the next mount merge (CR-092).
    addTombstone(convoId)
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== convoId)
      flushLocalNow(next)
      // Derive next active ID from fresh state (avoids stale closure)
      setActiveId((currentId) => {
        if (currentId !== convoId) return currentId
        return next[0]?.id ?? null
      })
      return next
    })
    enqueueServer(convoId, { kind: "delete" }, true)
  }, [addTombstone, flushLocalNow, enqueueServer])

  const replaceMessages = useCallback((convoId: string, newMessages: ChatMessage[]) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? markPrivate({ ...c, messages: newMessages, updatedAt: Date.now() }) : c,
      )
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      return next
    })
  }, [persist])

  /** Apply a compressed history summary, preserving any messages appended after
   *  the snapshot the compression ran on (e.g. the in-flight turn). Reads the
   *  conversation's CURRENT messages atomically here, so a compression that
   *  resolves after the turn advanced replaces only the summarized prefix
   *  instead of wholesale-overwriting and wiping the live turn (CR-003). */
  const mergeCompressedHistory = useCallback(
    (convoId: string, compressed: ChatMessage[], originalCount: number) => {
      setConversations((prev) => {
        const target = prev.find((c) => c.id === convoId)
        // Skip if the list shrank below the snapshot (cleared/replaced under us)
        // — the compression is stale; don't corrupt the conversation.
        if (!target || target.messages.length < originalCount) return prev
        const tail = target.messages.slice(originalCount)
        const merged = [...compressed, ...tail]
        const next = prev.map((c) =>
          c.id === convoId ? { ...c, messages: merged, updatedAt: Date.now() } : c,
        )
        const updated = next.find((c) => c.id === convoId)
        persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
        return next
      })
    },
    [persist],
  )

  const clearMessages = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, messages: [], updatedAt: Date.now() } : c,
      )
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      return next
    })
  }, [persist])

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
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      return next
    })
  }, [persist])

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

  const rename = useCallback((convoId: string, newTitle: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, title: newTitle, updatedAt: Date.now() } : c
      )
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      return next
    })
  }, [persist])

  const archive = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, archived: true, updatedAt: Date.now() } : c
      )
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      setActiveId((currentId) => {
        if (currentId !== convoId) return currentId
        const firstActive = next.find((c) => !c.archived)
        return firstActive?.id ?? null
      })
      return next
    })
  }, [persist])

  const unarchive = useCallback((convoId: string) => {
    setConversations((prev) => {
      const next = prev.map((c) =>
        c.id === convoId ? { ...c, archived: false, updatedAt: Date.now() } : c
      )
      const updated = next.find((c) => c.id === convoId)
      persist(next, { convoId, server: updated ? { kind: "upsert", convo: updated } : undefined })
      return next
    })
  }, [persist])

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
    for (const id of ids) addTombstone(id)
    setConversations((prev) => {
      const next = prev.filter((c) => !idSet.has(c.id))
      flushLocalNow(next)
      setActiveId((currentId) => {
        if (!currentId || !idSet.has(currentId)) return currentId
        return next[0]?.id ?? null
      })
      return next
    })
    for (const id of ids) enqueueServer(id, { kind: "delete" }, true)
  }, [addTombstone, flushLocalNow, enqueueServer])

  const bulkArchive = useCallback((ids: string[]) => {
    if (ids.length === 0) return
    const idSet = new Set(ids)
    setConversations((prev) => {
      const next = prev.map((c) =>
        idSet.has(c.id) ? { ...c, archived: true, updatedAt: Date.now() } : c
      )
      flushLocalNow(next)
      for (const c of next) {
        if (idSet.has(c.id)) enqueueServer(c.id, { kind: "upsert", convo: c })
      }
      setActiveId((currentId) => {
        if (!currentId || !idSet.has(currentId)) return currentId
        const firstActive = next.find((c) => !c.archived)
        return firstActive?.id ?? null
      })
      return next
    })
  }, [flushLocalNow, enqueueServer])

  // Hydrate from server on mount — per-conversation version-vector merge
  // (audit F-7). For each ID we compare `updatedAt` on both sides:
  //   - server-only      → add to local
  //   - local-only       → push to server
  //   - server newer     → replace local record with server's
  //   - local newer      → push local record to server (existing
  //                         fire-and-forget semantics)
  //   - equal/unset      → no-op
  // A tombstoned id (locally deleted, delete not yet acked) is never re-added;
  // the delete is re-attempted instead. A tombstone is only cleared when the
  // server actually acks the delete (flushServer's success path) — NOT when a
  // fetch merely omits the id, since an empty/partial response is ambiguous and
  // would drop a still-needed tombstone, resurrecting the conversation (CR-092).
  // The server's forgotten feed is read first: those ids are dropped locally and
  // neither re-added from a server row nor pushed back.
  const serverHydratedRef = useRef(false)
  useEffect(() => {
    if (serverHydratedRef.current) return
    serverHydratedRef.current = true

    let since: string | undefined
    try { since = localStorage.getItem(FORGOTTEN_CURSOR_KEY) ?? undefined } catch { since = undefined }
    fetchForgottenConversations(since)
      .catch(() => ({ items: [], cursor: since ?? null }))
      .then((feed) => {
        const forgotten = new Set(
          feed.items.filter((i) => i.state === "trashed" || i.state === "purged").map((i) => i.id),
        )
        dropLocal(forgotten)
        if (feed.cursor) {
          try { localStorage.setItem(FORGOTTEN_CURSOR_KEY, feed.cursor) } catch { /* storage unavailable */ }
        }
        return fetchSyncedConversations().then((serverConvos) => ({ serverConvos, forgotten }))
      })
      .then(({ serverConvos, forgotten }) => {
        setConversations((local) => {
          const tombstones = tombstonesRef.current
          const byId = new Map(local.map((c) => [c.id, c] as const))
          const serverIds = new Set<string>()
          let changed = false

          for (const sc of serverConvos) {
            serverIds.add(sc.id)
            if (forgotten.has(sc.id)) continue
            if (tombstones.has(sc.id)) {
              // Deleted locally but the server still has it → re-attempt the
              // delete and do NOT resurrect the record.
              enqueueServer(sc.id, { kind: "delete" })
              continue
            }
            const existing = byId.get(sc.id)
            if (!existing) {
              byId.set(sc.id, sc)
              changed = true
              continue
            }
            const localTs = existing.updatedAt ?? 0
            const serverTs = sc.updatedAt ?? 0
            if (serverTs > localTs) {
              byId.set(sc.id, sc)
              changed = true
            } else if (localTs > serverTs && !existing.private && !isPrivateModeActive()) {
              // Local has newer changes the server never received (e.g.
              // previous syncConversation() failed). Push now.
              pushConversation(existing)
            }
          }

          // Push any local-only conversations the server is missing (never a
          // tombstoned or forgotten id — that would re-create what was deleted).
          if (!isPrivateModeActive()) {
            for (const c of local) {
              if (!c.private && !serverIds.has(c.id) && !tombstones.has(c.id) && !forgotten.has(c.id)) {
                pushConversation(c)
              }
            }
          }

          if (!changed) return local
          const merged = Array.from(byId.values())
            .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
            .slice(0, MAX_CONVERSATIONS)
          saveConversations(merged)
          return merged
        })
      })
      .catch(() => { /* Server unavailable */ })
  }, [enqueueServer, dropLocal, pushConversation])

  return {
    conversations, visibleConversations, active, activeId, setActiveId,
    create, addMessage, updateLastMessage, updateLastMessageModel, updateModel, remove, rename,
    replaceMessages, mergeCompressedHistory, clearMessages,
    verifiedConversations, markVerified, clearVerified,
    saveVerification, getVerification, getAllVerificationReports,
    archive, unarchive, showArchived, toggleShowArchived, archivedCount,
    bulkDelete, bulkArchive,
    syncFailing,
  }
}
