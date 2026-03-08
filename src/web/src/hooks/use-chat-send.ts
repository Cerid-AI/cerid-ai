// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useState } from "react"
import type { ChatMessage, KBQueryResult, SourceRef } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { recommendModel } from "@/lib/model-router"
import { deduplicateChunks, formatChunkWithHeader } from "@/lib/kb-utils"
import { uuid } from "@/lib/utils"

interface UseChatSendOptions {
  // Conversation
  activeId: string | null
  activeMessages: ChatMessage[] | undefined
  create: (modelId: string) => string
  addMessage: (convoId: string, msg: ChatMessage) => void
  updateModel: (convoId: string, modelId: string) => void

  // Chat send primitive
  send: (convoId: string, messages: Pick<ChatMessage, "role" | "content">[], model: string, sources?: SourceRef[]) => void

  // Current model (owned by ChatPanel, shared with toolbar/dialog/correction)
  selectedModel: string
  setSelectedModel: (id: string) => void

  // Routing settings
  routingMode: string
  costSensitivity: number

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

export function useChatSend(options: UseChatSendOptions): UseChatSendReturn {
  const [autoRouteNotice, setAutoRouteNotice] = useState<string | null>(null)
  const [lastAutoInjectCount, setLastAutoInjectCount] = useState(0)

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
        const historyTokens = (options.activeMessages ?? []).reduce((sum, m) => sum + Math.ceil(m.content.length / 4), 0)
        const userTokens = Math.ceil(content.length / 4)
        const manualTokens = manuallyInjected.reduce((sum, r) => sum + Math.ceil(r.content.length / 4), 0)
        const reservedTokens = historyTokens + userTokens + manualTokens + 1200
        let remainingBudget = Math.floor(modelObj.contextWindow * 0.8) - reservedTokens

        const candidates = options.kbResults
          .filter((r) => r.relevance >= options.autoInjectThreshold && !injectedIds.has(r.artifact_id))
        for (const c of candidates) {
          const chunkTokens = Math.ceil(c.content.length / 4)
          if (chunkTokens > remainingBudget) break
          manuallyInjected.push(c)
          remainingBudget -= chunkTokens
          autoInjectedCount++
        }
      }

      let sourcesForAssistant: SourceRef[] | undefined
      const allMessages: Pick<ChatMessage, "role" | "content">[] = []
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
          content: `The user's knowledge base contains the following relevant context. Use it to inform your response:\n\n${contextParts.join("\n\n")}`,
        })
        options.clearInjected()
      }

      if (autoInjectedCount > 0) {
        setLastAutoInjectCount(autoInjectedCount)
      }

      allMessages.push(...(options.activeMessages ?? []), userMsg)
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
