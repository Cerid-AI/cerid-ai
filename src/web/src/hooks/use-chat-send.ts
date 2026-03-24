// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useRef, useState } from "react"
import type { ChatMessage, KBQueryResult, SourceRef } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { recommendModel } from "@/lib/model-router"
import { deduplicateChunks, formatChunkWithHeader } from "@/lib/kb-utils"
import { estimateTokenCount, uuid } from "@/lib/utils"
import { compressConversation } from "@/lib/api"

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
  send: (convoId: string, messages: Pick<ChatMessage, "role" | "content">[], model: string, sources?: SourceRef[]) => void

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

  // Callback before send (e.g. reset verification banner)
  onBeforeSend?: () => void
}

interface UseChatSendReturn {
  autoRouteNotice: string | null
  lastAutoInjectCount: number
  resetAutoInjectCount: () => void
  handleSend: (content: string) => void
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

  const handleSend = useCallback(
    (content: string) => {
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
      const manuallyInjected = [...options.injectedContext]
      const injectedIds = new Set(manuallyInjected.map((r) => r.artifact_id))

      // Auto-inject high-confidence KB results, limited by token budget
      let autoInjectedCount = 0
      if (options.autoInject && options.kbResults.length > 0) {
        const modelObj = MODELS.find((m) => m.id === modelToUse) ?? MODELS[0]
        // Reserve budget: conversation history + user message + response (~1000 tokens) + system overhead (~200)
        const historyTokens = (options.activeMessages ?? []).reduce((sum, m) => sum + estimateTokenCount(m.content), 0)
        const userTokens = estimateTokenCount(content)
        const manualTokens = manuallyInjected.reduce((sum, r) => sum + estimateTokenCount(r.content), 0)
        const reservedTokens = historyTokens + userTokens + manualTokens + 1200
        let remainingBudget = modelObj.effectiveContextWindow - reservedTokens

        const candidates = options.kbResults
          .filter((r) => r.relevance >= options.autoInjectThreshold && !injectedIds.has(r.artifact_id))
        for (const c of candidates) {
          const chunkTokens = estimateTokenCount(c.content)
          if (chunkTokens > remainingBudget) break
          manuallyInjected.push(c)
          remainingBudget -= chunkTokens
          autoInjectedCount++
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
        }))

        const contextParts = dedupedSources.map(formatChunkWithHeader)
        allMessages.push({
          role: "system",
          content: `Reference the following knowledge base documents when answering. Each <document> is an independent source.\n\n${contextParts.join("\n\n")}`,
        })
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

      options.send(convoId, allMessages, modelToUse, sourcesForAssistant)
      if (modelToUse !== options.selectedModel && options.activeId) {
        options.updateModel(options.activeId, modelToUse)
      }
    },
    [options],
  )

  const resetAutoInjectCount = useCallback(() => setLastAutoInjectCount(0), [])

  return { autoRouteNotice, lastAutoInjectCount, resetAutoInjectCount, handleSend }
}
