// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import type { ChatMessage, KBQueryResult, SourceRef } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { recommendModel } from "@/lib/model-router"
import { deduplicateChunks, formatChunkWithHeader, memoryToKBResult } from "@/lib/kb-utils"
import { estimateTokenCount, uuid } from "@/lib/utils"
import { compressConversation, queryKB, recallMemories } from "@/lib/api"

/** How many user+assistant pairs to keep in client-side sliding window fallback. */
const FALLBACK_KEEP_PAIRS = 3

/** Proportion of effective context window that triggers proactive pruning. */
const PRUNE_TRIGGER_RATIO = 0.7

/** Target proportion of effective context window after compression. */
const PRUNE_TARGET_RATIO = 0.5

interface UseChatSendOptions {
  // Conversation
  activeId: string | null
  activeMessages: ChatMessage[] | undefined
  create: (modelId: string) => string
  addMessage: (convoId: string, msg: ChatMessage) => void
  updateModel: (convoId: string, modelId: string) => void
  replaceMessages?: (convoId: string, msgs: ChatMessage[]) => void

  // Chat send primitive
  send: (convoId: string, messages: Pick<ChatMessage, "role" | "content">[], model: string, sources?: SourceRef[], degradedReason?: string) => void

  // Current model (owned by ChatPanel, shared with toolbar/dialog/correction)
  selectedModel: string
  setSelectedModel: (id: string) => void

  // Routing settings
  routingMode: string
  costSensitivity: "low" | "medium" | "high"

  // Auto-inject settings
  autoInject: boolean
  autoInjectThreshold: number

  // KB context
  injectedContext: KBQueryResult[]
  kbResults: KBQueryResult[]
  clearInjected: () => void

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
  handleSend: (content: string) => Promise<void>
}

/**
 * Client-side sliding window fallback.
 * Keeps system message + last N user/assistant pairs.
 */
function slidingWindowPrune(
  messages: Pick<ChatMessage, "role" | "content">[],
  keepPairs: number = FALLBACK_KEEP_PAIRS,
): Pick<ChatMessage, "role" | "content">[] {
  if (messages.length === 0) return messages

  const system = messages[0]?.role === "system" ? messages[0] : null
  const conversation = system ? messages.slice(1) : [...messages]

  const keepCount = keepPairs * 2
  if (conversation.length <= keepCount) return messages

  const recent = conversation.slice(-keepCount)
  const result: Pick<ChatMessage, "role" | "content">[] = []
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
    async (content: string) => {
      options.onBeforeSend?.()

      // Auto-routing: silently switch model if recommendation exists
      let modelToUse = options.selectedModel
      if (options.routingMode === "auto") {
        const currentObj = MODELS.find((m) => m.id === options.selectedModel) ?? MODELS[0]
        const rec = recommendModel(
          content, currentObj, options.activeMessages ?? [],
          options.injectedContext.length, options.costSensitivity,
        )
        if (rec.model.id !== options.selectedModel && rec.savingsVsCurrent > 0.0001) {
          modelToUse = rec.model.id
          options.setSelectedModel(modelToUse)
          setAutoRouteNotice(`Switched to ${rec.model.label}`)
          setTimeout(() => setAutoRouteNotice(null), 4000)
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

      // Combine manually injected + auto-injected context
      // Skip chunks already sent to the model in prior turns (session dedup)
      const manuallyInjected = [...options.injectedContext]
      const injectedIds = new Set(manuallyInjected.map((r) => r.artifact_id))
      const priorInjected = injectedHistoryRef.current

      // Auto-inject: query KB with the CURRENT message, with a 500ms timeout
      // to avoid blocking the stream start. Falls back to stale results on timeout.
      // IMPORTANT: AbortController ensures timed-out fetches release browser
      // connection slots immediately, preventing the chat/stream request from
      // being queued behind stale KB queries.
      let autoInjectedCount = 0
      // Skip auto-inject on the first message of a conversation — the KB
      // queries compete for browser connection slots and backend event loop
      // time, delaying the chat/stream response.  Follow-up messages benefit
      // more from context injection once the conversation topic is established.
      const isFirstMessage = !(options.activeMessages?.length)
      if (options.autoInject && !isFirstMessage) {
        let freshResults = options.kbResults
        // Only hit the network when the cache is cold. Wave-0 Task 3:
        // useOrchestratedQuery / useKBContext already populate TanStack
        // cache with staleTime 15s — re-firing queryKB here duplicates work
        // and saturates the backend _QUERY_SEMAPHORE.
        const cacheCold = !freshResults || freshResults.length === 0
        if (cacheCold && content.length > 2) {
          const injectAbort = new AbortController()
          const timeout = new Promise<null>((resolve) => setTimeout(() => {
            injectAbort.abort()
            resolve(null)
          }, 500))
          // Fire KB query and memory recall in parallel with shared timeout
          const [freshKB, freshMemories] = await Promise.all([
            Promise.race([queryKB(content, undefined, 5, undefined, { signal: injectAbort.signal }), timeout]).catch(() => null),
            Promise.race([recallMemories(content, 3).catch(() => []), timeout]).catch(() => []),
          ])
          if (freshKB?.results?.length) {
            freshResults = freshKB.results
          }
          // Merge memories into the candidate pool as pseudo-KB results
          if (Array.isArray(freshMemories) && freshMemories.length > 0) {
            for (const mem of freshMemories) {
              freshResults.push(memoryToKBResult(mem))
            }
          }
        } else if (!cacheCold && content.length > 2) {
          // Cache warm — skip queryKB but still pull fresh memories
          // (memories aren't covered by the TanStack KB cache).
          const freshMemories = await recallMemories(content, 3).catch(() => [])
          if (Array.isArray(freshMemories) && freshMemories.length > 0) {
            // Avoid mutating the caller's kbResults array (it's a React
            // state reference). Clone before pushing memory pseudo-results.
            freshResults = [...freshResults]
            for (const mem of freshMemories) {
              freshResults.push(memoryToKBResult(mem))
            }
          }
        }
        if (freshResults.length > 0) {
          const modelObj = MODELS.find((m) => m.id === modelToUse) ?? MODELS[0]
          const historyTokens = (options.activeMessages ?? []).reduce((sum, m) => sum + estimateTokenCount(m.content), 0)
          const userTokens = estimateTokenCount(content)
          const manualTokens = manuallyInjected.reduce((sum, r) => sum + estimateTokenCount(r.content), 0)
          const reservedTokens = historyTokens + userTokens + manualTokens + 1200
          let remainingBudget = modelObj.effectiveContextWindow - reservedTokens

          const candidates = freshResults
            .filter((r) => r.relevance >= options.autoInjectThreshold
              && !injectedIds.has(r.artifact_id)
              && !priorInjected.has(`${r.artifact_id}:${r.chunk_index}`))
          for (const c of candidates) {
            const chunkTokens = estimateTokenCount(c.content)
            if (chunkTokens > remainingBudget) break
            manuallyInjected.push(c)
            remainingBudget -= chunkTokens
            autoInjectedCount++
          }
        }
      }

      let sourcesForAssistant: SourceRef[] | undefined
      let allMessages: Pick<ChatMessage, "role" | "content">[] = []
      // Deduplicate overlapping chunks and format with domain headers
      const dedupedSources = deduplicateChunks(manuallyInjected)
      if (dedupedSources.length > 0) {
        sourcesForAssistant = dedupedSources.map((r) => ({
          artifact_id: r.artifact_id,
          filename: r.filename,
          domain: r.domain,
          sub_category: r.sub_category,
          relevance: r.relevance,
          chunk_index: r.chunk_index,
          tags: r.tags,
          quality_score: r.quality_score,
          source_type: r.source_url
            ? ("external" as const)
            : r.source_type === "memory"
              ? ("memory" as const)
              : ("kb" as const),
          source_url: r.source_url,
        }))

        // Separate documents from memories for distinct formatting
        const docSources = dedupedSources.filter((s) => s.source_type !== "memory")
        const memorySources = dedupedSources.filter((s) => s.source_type === "memory")
        const contextParts = docSources.map(formatChunkWithHeader)
        if (memorySources.length > 0) {
          const memParts = memorySources.map((m) => {
            const type = m.filename?.replace("memory:", "") ?? "fact"
            return `<memory type="${type}" relevance="${m.relevance.toFixed(2)}">\n${m.content}\n</memory>`
          })
          contextParts.push("\n[Remembered Context]\n" + memParts.join("\n"))
        }

        // Build prior-context summary so the model knows what it was shown before
        let priorContextNote = ""
        if (priorInjected.size > 0) {
          const priorFiles = new Set<string>()
          for (const key of priorInjected) {
            const msg = (options.activeMessages ?? []).find(
              (m) => m.sourcesUsed?.some((s) => `${s.artifact_id}:${s.chunk_index}` === key),
            )
            if (msg) {
              const src = msg.sourcesUsed?.find((s) => `${s.artifact_id}:${s.chunk_index}` === key)
              if (src?.filename) priorFiles.add(src.filename)
            }
          }
          if (priorFiles.size > 0) {
            priorContextNote = `\n\nNote: In earlier turns you were also shown content from: ${[...priorFiles].join(", ")}. That context is still relevant — refer to it as needed.`
          }
        }

        allMessages.push({
          role: "system",
          content: `The user has a personal knowledge base. Below are documents that may be relevant to this conversation. When these documents contain the answer, cite specific details and facts from them. When they do NOT contain the answer, use your general knowledge — never say "there is no information in your knowledge base" or refuse to answer. The user expects a helpful answer regardless of what the documents contain.${priorContextNote}\n\n${contextParts.join("\n\n")}`,
        })

        // Record injected chunks for session dedup on subsequent turns
        for (const s of dedupedSources) {
          priorInjected.add(`${s.artifact_id}:${s.chunk_index}`)
        }
        options.clearInjected()
      }

      if (autoInjectedCount > 0) {
        setLastAutoInjectCount(autoInjectedCount)
      }

      if (import.meta.env.DEV && allMessages[0]?.role === "system") {
        console.log(`[kb-inject] System message (${allMessages[0].content.length} chars) with ${dedupedSources.length} sources`)
      }

      allMessages.push(...(options.activeMessages ?? []), userMsg)

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
        allMessages = slidingWindowPrune(allMessages, FALLBACK_KEEP_PAIRS) as typeof allMessages

        // Fire-and-forget: compress via backend for higher-quality pruning
        // and persist the compressed history for future turns.
        if (options.replaceMessages && !compressingRef.current) {
          const capturedConvoId = convoId
          compressingRef.current = true
          compressConversation(
            (options.activeMessages ?? []).map((m) => ({ role: m.role, content: m.content })),
            targetTokens,
          )
            .then((result) => {
              // Map compressed messages back to ChatMessage format for persistence
              const compressed: ChatMessage[] = result.messages.map((m, i) => ({
                id: uuid(),
                role: m.role as ChatMessage["role"],
                content: m.content,
                timestamp: Date.now() - (result.messages.length - i) * 1000,
              }))
              options.replaceMessages!(capturedConvoId, compressed)
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

      options.send(convoId, allMessages, modelToUse, sourcesForAssistant, options.degradedReason)
      if (modelToUse !== options.selectedModel && options.activeId) {
        options.updateModel(options.activeId, modelToUse)
      }
    },
    [options],
  )

  const resetAutoInjectCount = useCallback(() => setLastAutoInjectCount(0), [])

  // Reset session injection history when conversation changes
  useEffect(() => {
    injectedHistoryRef.current = new Set()
  }, [options.activeId])

  return { autoRouteNotice, lastAutoInjectCount, resetAutoInjectCount, handleSend }
}
