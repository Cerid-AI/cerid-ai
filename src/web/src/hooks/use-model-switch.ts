// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { buildSwitchOptions } from "@/lib/model-router"
import { compressConversation, summarizeConversation } from "@/lib/api"
import { estimateTokenCount, uuid } from "@/lib/utils"
import { MODELS } from "@/lib/types"
import type { ChatMessage, ModelOption, ModelSwitchOptions, SwitchStrategy } from "@/lib/types"

interface UseModelSwitchOptions {
  currentModel: ModelOption
  messages: ChatMessage[]
  onModelChange: (modelId: string) => void
  onReplaceMessages: (messages: ChatMessage[]) => void
  onClearMessages: () => void
}

export function useModelSwitch({
  currentModel,
  messages,
  onModelChange,
  onReplaceMessages,
  onClearMessages,
}: UseModelSwitchOptions) {
  const [pendingSwitch, setPendingSwitch] = useState<ModelSwitchOptions | null>(null)
  const [isSummarizing, setIsSummarizing] = useState(false)

  const initSwitch = useCallback(
    (targetModelId: string, opts?: { autoRouted?: boolean }) => {
      const targetModel = MODELS.find((m) => m.id === targetModelId)
      if (!targetModel) return

      // No messages → direct switch, no dialog needed
      if (messages.length === 0) {
        onModelChange(targetModelId)
        return
      }

      // Auto-routed switch: auto-compress if history exceeds 70% of target context
      if (opts?.autoRouted) {
        const historyTokens = messages.reduce((sum, m) => sum + estimateTokenCount(m.content), 0)
        const threshold = Math.floor(targetModel.effectiveContextWindow * 0.7)
        if (historyTokens > threshold) {
          compressConversation(
            messages.map((m) => ({ role: m.role, content: m.content })),
            threshold,
          ).then(({ compressed_messages }) => {
            const compressedMsgs: ChatMessage[] = compressed_messages.map((m) => ({
              id: uuid(),
              role: m.role as ChatMessage["role"],
              content: m.content,
              timestamp: Date.now(),
            }))
            onReplaceMessages(compressedMsgs)
            onModelChange(targetModelId)
            console.log(`[model-switch] Auto-compressed: ${historyTokens} → ${threshold} tokens target`)
          }).catch((err) => {
            console.warn("[model-switch] Auto-compress failed, switching without compression:", err)
            onModelChange(targetModelId)
          })
          return
        }
        // Fits without compression
        onModelChange(targetModelId)
        return
      }

      const options = buildSwitchOptions(targetModel, currentModel, messages)
      setPendingSwitch(options)
    },
    [currentModel, messages, onModelChange, onReplaceMessages],
  )

  const executeSwitch = useCallback(
    async (strategy: SwitchStrategy) => {
      if (!pendingSwitch) return

      const targetModelId = pendingSwitch.targetModel.id

      switch (strategy) {
        case "continue":
          onModelChange(targetModelId)
          break

        case "summarize": {
          setIsSummarizing(true)
          try {
            const summary = await summarizeConversation(
              messages.map((m) => ({ role: m.role, content: m.content })),
              currentModel.id,
            )
            const summaryMessage: ChatMessage = {
              id: uuid(),
              role: "system",
              content: `[Conversation summary from ${currentModel.label}]\n\n${summary}`,
              timestamp: Date.now(),
            }
            onReplaceMessages([summaryMessage])
            onModelChange(targetModelId)
          } catch (err) {
            console.error("[model-switch] Summarization failed, falling back to continue:", err)
            onModelChange(targetModelId)
          } finally {
            setIsSummarizing(false)
          }
          break
        }

        case "fresh":
          onClearMessages()
          onModelChange(targetModelId)
          break
      }

      setPendingSwitch(null)
    },
    [pendingSwitch, currentModel, messages, onModelChange, onReplaceMessages, onClearMessages],
  )

  const cancelSwitch = useCallback(() => {
    setPendingSwitch(null)
  }, [])

  return {
    pendingSwitch,
    isSummarizing,
    initSwitch,
    executeSwitch,
    cancelSwitch,
  }
}
