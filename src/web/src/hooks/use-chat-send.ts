// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useCallback, useEffect, useRef, useState } from "react"
import type { ChatMessage, KBQueryResult, MemoryRecallResult, SourceRef } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { recommendModel } from "@/lib/model-router"
import { deduplicateChunks, formatChunkWithHeader, injectionKey, memoryToKBResult, selectDocsWithinBudget } from "@/lib/kb-utils"
import { estimateTokenCount, uuid } from "@/lib/utils"
import { compressConversation, queryKB, recallMemories } from "@/lib/api"
import { detectAsOf } from "@/lib/as-of"
import { RAG_SYSTEM_PREAMBLE } from "@/lib/rag-prompt"

/** How many user+assistant pairs to keep in client-side sliding window fallback. */
const FALLBACK_KEEP_PAIRS = 3

/** Proportion of effective context window that triggers proactive pruning. */
const PRUNE_TRIGGER_RATIO = 0.7

/** Target proportion of effective context window after compression. */
const PRUNE_TARGET_RATIO = 0.5

/** Fallback hard cap on auto-injected chunks per turn, mirroring the server's
 *  AUTO_INJECT_MAX default (src/mcp/config/settings.py). Used when the caller
 *  does not pass options.autoInjectMax. */
const DEFAULT_AUTO_INJECT_MAX = 3

/** How long a send waits for the knowledge base. Every question waits for it:
 *  at 500 ms no answer was grounded on a host whose retrieval takes 1.7 to
 *  3.9 s (twelve live queries, 2026-09-27), and an early answer without the
 *  user's documents is worth less than a later one with them. A host that
 *  answers quickly never reaches this. Past it the knowledge base is not
 *  slow, it is not answering, and that is reported as degradation for the
 *  questions that cannot be answered without it. */
const KB_WAIT_TIMEOUT_MS = 5_000

/** Rows a send asks the knowledge base for. The panel's own retrieval asks for
 *  10 (use-kb-context.ts, use-orchestrated-query.ts), so a send that asked for
 *  fewer chose from a different pool than the one the panel shows. */
const SEND_KB_TOP_K = 10

/** Outcome of the auto-inject KB round trip. A plain `null` cannot say
 *  whether the KB had nothing, took too long, or failed — and the caller has
 *  to tell those apart to report the last two honestly. */
type KBInjectOutcome =
  | { kind: "ok"; value: Awaited<ReturnType<typeof queryKB>> }
  | { kind: "timeout" }
  | { kind: "error" }

/** The same three answers for the memory leg, which used to turn a timeout or
 *  a failure into an empty list. */
type MemoryRecallOutcome =
  | { kind: "ok"; value: MemoryRecallResult[] }
  | { kind: "timeout" }
  | { kind: "error" }

/** Recall memories for a send, waiting no longer than the knowledge base. */
async function recallWithinWait(content: string, asOf: string | undefined): Promise<MemoryRecallOutcome> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<MemoryRecallOutcome>((resolve) => {
    timer = setTimeout(() => resolve({ kind: "timeout" }), KB_WAIT_TIMEOUT_MS)
  })
  const recall: Promise<MemoryRecallOutcome> = recallMemories(content, 3, undefined, asOf)
    .then((value) => ({ kind: "ok" as const, value: Array.isArray(value) ? value : [] }))
    .catch(() => ({ kind: "error" as const }))
  try {
    return await Promise.race([recall, timeout])
  } finally {
    clearTimeout(timer)
  }
}

/** Give compressed history back the identity and evidence of the messages it
 *  came from. The server returns the turns it kept verbatim (role and content
 *  unchanged) around a new summary; each of those is matched, in order, to its
 *  original so its id, sourcesUsed and degradedReason survive. A message with
 *  no original (the summary) carries the sources of the turns it replaced, so
 *  later turns can still name the files those turns were shown. Exported for
 *  tests. */
export function restoreCompressedHistory(
  original: ChatMessage[],
  compressed: Pick<ChatMessage, "role" | "content">[],
  now: number = Date.now(),
): ChatMessage[] {
  const matched = new Set<number>()
  let cursor = 0
  const restored: (ChatMessage | null)[] = compressed.map((m) => {
    for (let j = cursor; j < original.length; j++) {
      if (original[j].role === m.role && original[j].content === m.content) {
        matched.add(j)
        cursor = j + 1
        return original[j]
      }
    }
    return null
  })
  const replacedSources = new Map<string, SourceRef>()
  original.forEach((m, j) => {
    if (matched.has(j)) return
    for (const s of m.sourcesUsed ?? []) replacedSources.set(injectionKey(s), s)
  })
  let sourcesCarried = false
  return restored.map((m, i) => {
    if (m) return m
    const msg: ChatMessage = {
      id: uuid(),
      role: compressed[i].role as ChatMessage["role"],
      content: compressed[i].content,
      timestamp: now - (compressed.length - i) * 1000,
    }
    if (!sourcesCarried && replacedSources.size > 0) {
      msg.sourcesUsed = [...replacedSources.values()]
      sourcesCarried = true
    }
    return msg
  })
}

/** The prior-context note names files from earlier turns, and only from
 *  turns the model is actually sent: after the sliding window drops a turn,
 *  naming its files tells the model it can see something it cannot. */
function priorContextNoteFor(priorInjected: Set<string>, visible: Pick<ChatMessage, "sourcesUsed">[]): string {
  if (priorInjected.size === 0) return ""
  const priorFiles = new Set<string>()
  for (const key of priorInjected) {
    for (const m of visible) {
      const src = m.sourcesUsed?.find((s) => injectionKey(s) === key)
      if (src) {
        if (src.filename) priorFiles.add(src.filename)
        break
      }
    }
  }
  if (priorFiles.size === 0) return ""
  return `\n\nNote: In earlier turns you were also shown content from: ${[...priorFiles].join(", ")}. That context is still relevant — refer to it as needed.`
}

/** Questions about the user's own data — the ones an LLM cannot answer
 *  without KB grounding and will instead hallucinate a denial for
 *  ("I don't have access to your Apple Mail"). Deliberately possessive/
 *  first-person anchored so general-knowledge questions never match. */
const PERSONAL_DATA_QUERY_RE =
  /\b(my|mine|our)\b|\bin my\b|\bfrom my\b|\b(did|do|have|am|was|can) i\b|\bi (received|sent|got|paid|bought|wrote|said|saved|uploaded)\b/i

/** True when the message asks about the user's personal data (mail, files,
 *  purchases, …) rather than general knowledge. Exported for tests. */
export function isPersonalDataQuery(content: string): boolean {
  return PERSONAL_DATA_QUERY_RE.test(content)
}

/** Honest deferral streamed in place of an LLM answer when retrieval is
 *  degraded and a personal-data question has zero grounding (sf-2 / UX-01):
 *  an ungrounded model reliably fabricates "I don't have access to X". */
export function buildDeferralMessage(reason = ""): string {
  // Name the failure. "I couldn't search" alone reads the same whether the
  // backend is down or the knowledge base simply held nothing, and those two
  // call for different actions from the user.
  const cause = /failed|error/i.test(reason)
    ? "Your knowledge base could not be reached, so I won't "
    : /did not answer|exceeded|timed? out|budget/i.test(reason)
      ? "Your knowledge base did not answer in time, so I won't "
      : "I couldn't search your knowledge base for this just now, so I won't "
  return (
    cause +
    "guess about your personal data. Please try again in a moment — or " +
    "narrow the question to a specific domain (for example \"in my mail\") " +
    "to lighten the search."
  )
}

interface UseChatSendOptions {
  // Conversation
  activeId: string | null
  activeMessages: ChatMessage[] | undefined
  create: (modelId: string) => string
  addMessage: (convoId: string, msg: ChatMessage) => void
  updateModel: (convoId: string, modelId: string) => void
  replaceMessages?: (convoId: string, msgs: ChatMessage[]) => void
  /** Apply a compressed history summary, preserving messages appended after the
   *  `originalCount`-length snapshot the compression ran on (CR-003). */
  mergeCompressedHistory?: (convoId: string, compressed: ChatMessage[], originalCount: number) => void

  // Chat send primitive
  send: (convoId: string, messages: Pick<ChatMessage, "role" | "content">[], model: string, sources?: SourceRef[], degradedReason?: string) => void
  /** Abort the in-flight stream. A regenerate calls it before replacing the
   *  last turn so the aborted stream cannot write into the new answer. */
  stop?: () => void

  // Current model (owned by ChatPanel, shared with toolbar/dialog/correction)
  selectedModel: string
  setSelectedModel: (id: string) => void

  // Routing settings
  routingMode: string
  costSensitivity: "low" | "medium" | "high"

  // Auto-inject settings
  autoInject: boolean
  autoInjectThreshold: number
  /** Hard cap on the number of chunks auto-injected per turn, mirroring the
   *  server's AUTO_INJECT_MAX (config/settings.py). Independent of the token
   *  budget below — without this the loop was bounded only by remainingBudget.
   *  Defaults to DEFAULT_AUTO_INJECT_MAX when omitted. */
  autoInjectMax?: number
  /** Include knowledge packs in KB retrieval (Slice 7.3). Default true;
      when false, queryKB sends exclude_packs so packs are dropped. */
  includePacks: boolean

  // KB context
  injectedContext: KBQueryResult[]
  kbResults: KBQueryResult[]
  /** The text `kbResults` was retrieved for. The panel queries with the last
   *  message already in the conversation, so at send time its results belong
   *  to the previous question; they stand in for a query only when this
   *  equals the text being sent (a regenerate). */
  kbResultsQuery?: string
  /** Retrieval for the text being sent, shared with the panel's own request.
   *  Absent, the send queries the knowledge base itself. */
  retrieve?: (content: string) => Promise<Awaited<ReturnType<typeof queryKB>>>
  /** The domain filter in force in the knowledge panel. A send that asks the
   *  knowledge base itself (no shared `retrieve`, or a historical question)
   *  searches the same domains the panel does. */
  kbDomains?: string[]
  /** Remove the given pins from the manual context. The send passes the pins
   *  its prompt carried; a pin the context budget dropped stays pinned. */
  clearInjected: (sent: KBQueryResult[]) => void

  /** Private Mode level (0=off, 1=skip saves, 2=skip KB, 3=skip audit,
   *  4=full ephemeral; docs/PRIVATE_MODE.md). At level >= 2 the send path injects NO KB documents or
   *  memories, so the model sees only what the user types. Enforced here — the
   *  single payload-assembly boundary — because the context is assembled
   *  client-side and a backend gate cannot un-inject it. */
  privateModeLevel: number

  /** E1 R16 / CR-016: when false, skip client-side ``recallMemories`` auto-inject
   *  so Memory-off matches the toolbar contextSources.memory toggle. Defaults true. */
  memoryEnabled?: boolean

  /** Non-empty when retrieval breached its time budget for the current query.
   *  Propagated onto the assistant ChatMessage so MessageBubble can render a
   *  warning banner explaining the answer is ungrounded. */
  degradedReason?: string

  // Callback before send (e.g. reset verification banner)
  onBeforeSend?: () => void
}

interface UseChatSendReturn {
  autoRouteNotice: string | null
  lastAutoInjectCount: number
  resetAutoInjectCount: () => void
  handleSend: (content: string, baseMessages?: ChatMessage[]) => Promise<void>
  /** Re-send the last user turn and replace its answer (D20-A). */
  handleRegenerate: () => Promise<void>
}

/**
 * Client-side sliding window fallback.
 * Keeps system message + last N user/assistant pairs.
 */
function slidingWindowPrune<T extends Pick<ChatMessage, "role" | "content">>(
  messages: T[],
  keepPairs: number = FALLBACK_KEEP_PAIRS,
): T[] {
  if (messages.length === 0) return messages

  const system = messages[0]?.role === "system" ? messages[0] : null
  const conversation = system ? messages.slice(1) : [...messages]

  const keepCount = keepPairs * 2
  if (conversation.length <= keepCount) return messages

  const recent = conversation.slice(-keepCount)
  const result: T[] = []
  if (system) result.push(system)
  result.push(...recent)
  return result
}

export function useChatSend(options: UseChatSendOptions): UseChatSendReturn {
  const [autoRouteNotice, setAutoRouteNotice] = useState<string | null>(null)
  const [lastAutoInjectCount, setLastAutoInjectCount] = useState(0)
  // Prevent overlapping compression calls
  const compressingRef = useRef(false)
  // Track which artifact chunks have already been injected this session
  // so we don't re-send identical context to the model on follow-up turns
  const injectedHistoryRef = useRef<Set<string>>(new Set())

  const handleSend = useCallback(
    async (content: string, baseMessages?: ChatMessage[]) => {
      options.onBeforeSend?.()

      // E1 CR-079: the conversation history this send builds on. A correction
      // re-send passes the already-truncated history explicitly (baseMessages)
      // rather than relying on options.activeMessages — which is a closure over
      // the pre-truncation render and would otherwise re-send the stale history.
      const history = baseMessages ?? options.activeMessages ?? []

      // Auto-routing: silently switch model if recommendation exists
      let modelToUse = options.selectedModel
      if (options.routingMode === "auto") {
        const currentObj = MODELS.find((m) => m.id === options.selectedModel) ?? MODELS[0]
        const rec = recommendModel(
          content, currentObj, history,
          options.injectedContext.length, options.costSensitivity,
        )
        if (rec.model.id !== options.selectedModel && rec.savingsVsCurrent > 0.0001) {
          modelToUse = rec.model.id
          options.setSelectedModel(modelToUse)
          // UX-03: the combobox rewrite must be explained at the moment of
          // swap — the model name alone reads as the UI silently overriding
          // the user's selection. rec.reasoning carries the why.
          setAutoRouteNotice(
            rec.reasoning
              ? `Smart routing switched to ${rec.model.label}: ${rec.reasoning}`
              : `Smart routing switched to ${rec.model.label}`,
          )
          setTimeout(() => setAutoRouteNotice(null), 8000)
        }
      }

      let convoId = options.activeId
      if (!convoId) {
        convoId = options.create(modelToUse)
      }

      const userMsg: ChatMessage = {
        id: uuid(),
        role: "user",
        content,
        timestamp: Date.now(),
      }
      options.addMessage(convoId, userMsg)

      // Private Mode L2+ ("bypass KB injection — model sees only what you type"):
      // inject NOTHING — no manual context, no auto-inject, no memory recall.
      // This is the single boundary that enforces the toolbar's privacy promise.
      const bypassKB = options.privateModeLevel >= 2

      // E1 R7 / CR-079: correction re-send passes baseMessages (truncated history).
      // Clear session inject dedup so chunks from discarded answers do not block
      // re-injection for the correction (the message that most needs grounding).
      if (baseMessages != null) {
        injectedHistoryRef.current = new Set()
      }

      // Combine manually injected + auto-injected context
      // Skip chunks already sent to the model in prior turns (session dedup)
      const manualPins = bypassKB ? [] : [...options.injectedContext]
      const manuallyInjected = [...manualPins]
      // A pin excludes its own chunk, not every chunk of the same artifact.
      const pinnedKeys = new Set(manualPins.map(injectionKey))
      const priorInjected = injectedHistoryRef.current
      // What earlier turns were shown, before this turn adds to it.
      const shownBefore = new Set(priorInjected)

      // Auto-inject: query KB with the CURRENT message and wait for it up to
      // KB_WAIT_TIMEOUT_MS. On timeout the send goes out with no KB context.
      // IMPORTANT: AbortController ensures timed-out fetches release browser
      // connection slots immediately, preventing the chat/stream request from
      // being queued behind stale KB queries.
      let autoInjectedCount = 0
      // Freshest degradation signal for THIS send: the panel-level
      // degradedReason (from the orchestrated context query), superseded by
      // the send-time KB query's own envelope when one fires below.
      let effectiveDegradedReason = options.degradedReason ?? ""
      if (options.autoInject && !bypassKB) {
        // An explicitly historical question ("what was it in 2023") retrieves
        // what was in force then; the panel's results are current, so they
        // never stand in for it.
        const asOf = detectAsOf(content) ?? undefined
        const cacheWarm =
          !asOf && options.kbResults.length > 0 && options.kbResultsQuery === content
        let freshResults: KBQueryResult[] = cacheWarm ? options.kbResults : []
        const groundingCritical = isPersonalDataQuery(content)
        // E1 R16: honor contextSources.memory — do not recall when Memory is off.
        const memoryOn = options.memoryEnabled !== false
        let memoryOutcome: MemoryRecallOutcome = { kind: "ok", value: [] }
        // Only hit the network when the panel has not already retrieved for
        // this text. Wave-0 Task 3: useOrchestratedQuery / useKBContext
        // populate the TanStack cache — re-firing queryKB for the same text
        // duplicates work and saturates the backend _QUERY_SEMAPHORE.
        const cacheCold = !cacheWarm
        if (cacheCold && content.length > 2) {
          const injectAbort = new AbortController()
          const timers: ReturnType<typeof setTimeout>[] = []
          const waitTimeout = new Promise<KBInjectOutcome>((resolve) => {
            timers.push(setTimeout(() => resolve({ kind: "timeout" }), KB_WAIT_TIMEOUT_MS))
          })
          // The KB leg is tagged so a 5xx, an abort and a genuinely empty
          // knowledge base stay three different answers all the way to the
          // degraded-reason check below — collapsing them into `null` is what
          // let a KB outage render as "you have nothing about that".
          // A historical question asks the KB directly with as_of: the shared
          // retrieval (and its cache) answers for the present. Asked directly,
          // it carries the domain filter the panel is using.
          const kbLeg: Promise<KBInjectOutcome> = (
            options.retrieve && !asOf
              ? options.retrieve(content)
              : queryKB(
                  content,
                  options.kbDomains?.length ? options.kbDomains : undefined,
                  SEND_KB_TOP_K,
                  undefined,
                  {
                    signal: injectAbort.signal,
                    excludePacks: !options.includePacks,
                    asOf,
                  },
                )
          )
            .then((value) => ({ kind: "ok" as const, value }))
            .catch(() => ({ kind: "error" as const }))
          let kbOutcome: KBInjectOutcome
          try {
            ;[kbOutcome, memoryOutcome] = await Promise.all([
              Promise.race<KBInjectOutcome>([kbLeg, waitTimeout]),
              memoryOn ? recallWithinWait(content, asOf) : Promise.resolve(memoryOutcome),
            ])
          } finally {
            for (const t of timers) clearTimeout(t)
            // Release the send's own request once the wait is decided. The
            // abort used to sit on a timer that the line above cleared, so a
            // request that outran the wait ran on and held a backend slot.
            // It follows the verdict, so a knowledge base that never answered
            // is not reported as one that failed. A shared request is the
            // panel's and is not the send's to cancel.
            injectAbort.abort()
          }
          const freshKB = kbOutcome.kind === "ok" ? kbOutcome.value : null
          if (freshKB?.results?.length) {
            freshResults = freshKB.results
          }
          if (kbOutcome.kind === "timeout") {
            if (groundingCritical) {
              effectiveDegradedReason = `Knowledge base did not answer within ${KB_WAIT_TIMEOUT_MS}ms`
            }
          } else if (kbOutcome.kind === "error") {
            effectiveDegradedReason = "KB search failed"
          } else if (freshKB?.degraded_reason) {
            effectiveDegradedReason = freshKB.degraded_reason
          }
        } else if (!cacheCold && content.length > 2 && memoryOn) {
          // Cache warm — skip queryKB but still pull fresh memories (they
          // aren't covered by the TanStack KB cache), under the same wait as
          // the cold path: without it a slow recall held the send.
          memoryOutcome = await recallWithinWait(content, asOf)
        }
        // Merge memories into the candidate pool as pseudo-KB results (a new
        // array: kbResults is the caller's React state). A recall that failed
        // or never answered is reported the way the knowledge base's is,
        // unless the knowledge base already gave a reason.
        if (memoryOutcome.kind === "ok") {
          if (memoryOutcome.value.length > 0) {
            freshResults = [...freshResults, ...memoryOutcome.value.map(memoryToKBResult)]
          }
        } else if (!effectiveDegradedReason) {
          if (memoryOutcome.kind === "error") {
            effectiveDegradedReason = "Memory recall failed"
          } else if (groundingCritical) {
            effectiveDegradedReason = `Memory recall did not answer within ${KB_WAIT_TIMEOUT_MS}ms`
          }
        }
        if (freshResults.length > 0) {
          const modelObj = MODELS.find((m) => m.id === modelToUse) ?? MODELS[0]
          const historyTokens = history.reduce((sum, m) => sum + estimateTokenCount(m.content), 0)
          const userTokens = estimateTokenCount(content)
          const manualTokens = manuallyInjected.reduce((sum, r) => sum + estimateTokenCount(r.content), 0)
          const reservedTokens = historyTokens + userTokens + manualTokens + 1200
          let remainingBudget = modelObj.effectiveContextWindow - reservedTokens

          // CR-010: post-rerank relevance is an ordinal cross-encoder sigmoid
          // (provider-dependent scale), so an absolute threshold could suppress
          // a correct top match. Gate each candidate on its score RELATIVE to
          // the best available hit — autoInjectThreshold now means "fraction of
          // the top match" (e.g. 0.40 ⇒ keep chunks scoring ≥ 40% of the best).
          // A memory's relevance is recall salience, not a rerank score, so each
          // kind is gated against the best hit on its own scale: one strong
          // memory must not raise the floor over every document, or vice versa.
          const isMemory = (r: KBQueryResult) => r.source_type === "memory"
          const floorFor = (rows: KBQueryResult[]) => {
            const top = rows.reduce((mx, r) => Math.max(mx, r.relevance), 0)
            return top > 0 ? options.autoInjectThreshold * top : 0
          }
          const docFloor = floorFor(freshResults.filter((r) => !isMemory(r)))
          const memoryFloor = floorFor(freshResults.filter(isMemory))
          const candidates = freshResults
            .filter((r) => r.relevance >= (isMemory(r) ? memoryFloor : docFloor)
              && !pinnedKeys.has(injectionKey(r))
              && !priorInjected.has(injectionKey(r)))
          const autoInjectMax = options.autoInjectMax ?? DEFAULT_AUTO_INJECT_MAX
          for (const c of candidates) {
            if (autoInjectedCount >= autoInjectMax) break
            const chunkTokens = estimateTokenCount(c.content)
            // A chunk too large for what is left is skipped, not the end of
            // the list: a smaller, lower-ranked chunk may still fit.
            if (chunkTokens > remainingBudget) continue
            manuallyInjected.push(c)
            remainingBudget -= chunkTokens
            autoInjectedCount++
          }
        }
      }

      let sourcesForAssistant: SourceRef[] | undefined
      let allMessages: Pick<ChatMessage, "role" | "content" | "sourcesUsed">[] = []
      // The injected context, kept so the system message can be rebuilt for
      // the turns that survive pruning.
      let ragContext: string | null = null
      const ragSystemFor = (visible: Pick<ChatMessage, "sourcesUsed">[]) =>
        `${RAG_SYSTEM_PREAMBLE}${priorContextNoteFor(shownBefore, visible)}\n\n${ragContext}`
      // Deduplicate overlapping chunks and format with domain headers
      const dedupedSources = deduplicateChunks(manuallyInjected)
      if (dedupedSources.length > 0) {
        // Separate documents from memories for distinct formatting
        const docSources = dedupedSources.filter((s) => s.source_type !== "memory")
        const memorySources = dedupedSources.filter((s) => s.source_type === "memory")
        // Phase 2.4: apply per-model-family char budget at document granularity.
        // Documents are already sorted by descending relevance; whole docs are kept
        // or dropped — never truncated mid-document.
        const { selected: budgetedDocs, dropped } = selectDocsWithinBudget(docSources, modelToUse)
        // What the answer credits, and what later turns treat as already shown,
        // is what this prompt carries: a document the budget dropped was never seen.
        const shown = [...budgetedDocs, ...memorySources]
        sourcesForAssistant = shown.map((r) => ({
          artifact_id: r.artifact_id,
          filename: r.filename,
          domain: r.domain,
          sub_category: r.sub_category,
          relevance: r.relevance,
          chunk_index: r.chunk_index,
          tags: r.tags,
          quality_score: r.quality_score,
          source_type: r.source_type === "memory"
            ? ("memory" as const)
            : r.source_url
              ? ("external" as const)
              : ("kb" as const),
          source_url: r.source_url,
        }))
        const contextParts = budgetedDocs.map(formatChunkWithHeader)
        if (memorySources.length > 0) {
          const memParts = memorySources.map((m) => {
            const type = m.filename?.replace("memory:", "") ?? "fact"
            return `<memory type="${type}" relevance="${m.relevance.toFixed(2)}">\n${m.content}\n</memory>`
          })
          contextParts.push("\n[Remembered Context]\n" + memParts.join("\n"))
        }
        ragContext = contextParts.join("\n\n")

        allMessages.push({ role: "system", content: ragSystemFor(history) })

        // Record injected chunks for session dedup on subsequent turns
        for (const s of shown) {
          priorInjected.add(injectionKey(s))
        }
        // The pins this prompt carries leave the pin list; a pin the budget
        // dropped was never sent, and stays for the user to see.
        const droppedKeys = new Set(dropped.map(injectionKey))
        const sentPins = manualPins.filter((r) => !droppedKeys.has(injectionKey(r)))
        if (sentPins.length > 0) options.clearInjected(sentPins)
      }

      if (autoInjectedCount > 0) {
        setLastAutoInjectCount(autoInjectedCount)
      }

      // Honest degradation (sf-2 / UX-01): retrieval breached its budget, the
      // question is about the user's own data, and ZERO grounding reached this
      // send — streaming the LLM now can only produce a fabricated denial
      // ("I don't have access to your Apple Mail"). Defer honestly instead.
      // Private Mode L2+ is exempt: the user explicitly chose ungrounded chat.
      if (
        effectiveDegradedReason &&
        !bypassKB &&
        dedupedSources.length === 0 &&
        isPersonalDataQuery(content)
      ) {
        options.addMessage(convoId, {
          id: uuid(),
          role: "assistant",
          content: buildDeferralMessage(effectiveDegradedReason),
          timestamp: Date.now(),
          degradedReason: effectiveDegradedReason,
          deferral: true,
        })
        return
      }

      if (import.meta.env.DEV && allMessages[0]?.role === "system") {
        console.log(`[kb-inject] System message (${allMessages[0].content.length} chars) with ${dedupedSources.length} sources`)
      }

      allMessages.push(...history, userMsg)

      // ── Proactive history pruning ──
      // If conversation history exceeds 70% of effective context window,
      // prune before sending to avoid model context overflow.
      const modelObj = MODELS.find((m) => m.id === modelToUse) ?? MODELS[0]
      const totalTokens = allMessages.reduce((sum, m) => sum + estimateTokenCount(m.content), 0)
      const threshold = modelObj.effectiveContextWindow * PRUNE_TRIGGER_RATIO

      if (totalTokens > threshold) {
        const targetTokens = Math.floor(modelObj.effectiveContextWindow * PRUNE_TARGET_RATIO)

        // Immediately apply client-side sliding window for this send
        // (guarantees we don't exceed context window even if compress fails)
        allMessages = slidingWindowPrune(allMessages, FALLBACK_KEEP_PAIRS)
        // The note names only files from turns that are still sent.
        if (ragContext !== null) {
          allMessages[0] = { role: "system", content: ragSystemFor(allMessages.slice(1)) }
        }

        // Fire-and-forget: compress via backend for higher-quality pruning
        // and persist the compressed history for future turns.
        if (options.mergeCompressedHistory && !compressingRef.current) {
          const capturedConvoId = convoId
          // Snapshot the exact history the compression runs on. On resolution we
          // replace only these first `originalCount` messages, preserving the
          // in-flight turn appended during the async window (CR-003) — a
          // wholesale replace here wiped the current user+assistant turn.
          const snapshot = history
          const originalCount = snapshot.length
          compressingRef.current = true
          compressConversation(
            snapshot.map((m) => ({ role: m.role, content: m.content })),
            targetTokens,
          )
            .then((result) => {
              const compressed = restoreCompressedHistory(snapshot, result.messages as Pick<ChatMessage, "role" | "content">[])
              options.mergeCompressedHistory!(capturedConvoId, compressed, originalCount)
              if (import.meta.env.DEV) {
                console.log(
                  `[history] Compressed ${result.original_tokens} → ${result.compressed_tokens} tokens ` +
                  `(${Math.round((1 - result.compressed_tokens / result.original_tokens) * 100)}% reduction)`,
                )
              }
            })
            .catch((err) => {
              if (import.meta.env.DEV) {
                console.warn("[history] Backend compression failed, using sliding window:", err)
              }
            })
            .finally(() => {
              compressingRef.current = false
            })
        }
      }

      options.send(convoId, allMessages, modelToUse, sourcesForAssistant, effectiveDegradedReason || undefined)
      if (modelToUse !== options.selectedModel && options.activeId) {
        options.updateModel(options.activeId, modelToUse)
      }
    },
    [options],
  )

  const resetAutoInjectCount = useCallback(() => setLastAutoInjectCount(0), [])

  // D20-A: regenerate the last answer. The last user turn is re-sent through
  // handleSend — the same payload assembly as a normal send, so it gets the
  // same retrieval (the panel's kbResults for that text stand in via
  // kbResultsQuery), private-mode gating and routing. The conversation is
  // truncated to before that turn first so the answer is replaced, not
  // appended, and the in-flight stream is cancelled so its abort cannot
  // land on the new message.
  const handleRegenerate = useCallback(async () => {
    const convoId = options.activeId
    const msgs = options.activeMessages ?? []
    const lastUserIdx = msgs.findLastIndex((m) => m.role === "user")
    if (!convoId || lastUserIdx < 0 || !options.replaceMessages) return
    const lastUser = msgs[lastUserIdx]
    options.stop?.()
    const before = msgs.slice(0, lastUserIdx)
    options.replaceMessages(convoId, before)
    await handleSend(lastUser.content, before)
  }, [options, handleSend])

  // Reset session injection history when conversation changes
  useEffect(() => {
    injectedHistoryRef.current = new Set()
  }, [options.activeId])

  return { autoRouteNotice, lastAutoInjectCount, resetAutoInjectCount, handleSend, handleRegenerate }
}
