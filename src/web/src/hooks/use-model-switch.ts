// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { buildSwitchOptions } from "@/lib/model-router"
import { summarizeConversation } from "@/lib/api"
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
    (targetModelId: string) => {
      const targetModel = MODELS.find((m) => m.id === targetModelId)
      if (!targetModel) return

      // No messages → direct switch, no dialog needed
      if (messages.length === 0) {
        onModelChange(targetModelId)
        return
      }

      const options = buildSwitchOptions(targetModel, currentModel, messages)
      setPendingSwitch(options)
    },
    [currentModel, messages, onModelChange],
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
              id: crypto.randomUUID(),
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
