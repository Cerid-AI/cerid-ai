// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useMemo } from "react"
import { MODELS } from "@/lib/types"
import type { ChatMessage, LiveMetrics, ModelOption } from "@/lib/types"
import { estimateTokens } from "@/lib/utils"

export function useLiveMetrics(model: string, messages: ChatMessage[]) {
  const [streamingOutputChars, setStreamingOutputChars] = useState(0)

  const modelInfo: ModelOption | undefined = useMemo(
    () => MODELS.find((m) => m.id === model),
    [model],
  )

  const metrics: LiveMetrics = useMemo(() => {
    const { input: inputTokens, output: baseOutputTokens } = estimateTokens(messages)

    const outputTokens = baseOutputTokens + Math.ceil(streamingOutputChars / 4)

    const contextWindow = modelInfo?.contextWindow ?? 128_000
    const totalTokens = inputTokens + outputTokens
    const contextPct = contextWindow > 0 ? (totalTokens / contextWindow) * 100 : 0

    const sessionCost = modelInfo
      ? (inputTokens * modelInfo.inputCostPer1M + outputTokens * modelInfo.outputCostPer1M) / 1_000_000
      : 0

    let messageCost = 0
    if (modelInfo && messages.length >= 2) {
      const lastAssistant = messages.findLast((m) => m.role === "assistant")
      const lastUser = messages.findLast((m) => m.role === "user")
      if (lastAssistant && lastUser) {
        const inTok = Math.ceil(lastUser.content.length / 4)
        const outTok = Math.ceil(lastAssistant.content.length / 4)
        messageCost = (inTok * modelInfo.inputCostPer1M + outTok * modelInfo.outputCostPer1M) / 1_000_000
      }
    }

    return {
      inputTokens,
      outputTokens,
      contextPct: Math.min(contextPct, 100),
      sessionCost,
      messageCost,
      messagesCount: messages.length,
    }
  }, [messages, modelInfo, streamingOutputChars])

  const addStreamingChars = useCallback((chars: number) => {
    setStreamingOutputChars((prev) => prev + chars)
  }, [])

  const resetStreaming = useCallback(() => {
    setStreamingOutputChars(0)
  }, [])

  return { metrics, addStreamingChars, resetStreaming, modelInfo }
}