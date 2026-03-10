// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useMemo, useRef } from "react"
import { MODELS } from "@/lib/types"
import type { ChatMessage, LiveMetrics, ModelOption } from "@/lib/types"
import { estimateTokens, tokenCost } from "@/lib/utils"

/** Chars-per-tick threshold (~100 tokens). Re-renders only fire when crossing a tick boundary. */
const CHARS_PER_TICK = 400

export function useLiveMetrics(model: string, messages: ChatMessage[]) {
  const streamingCharsRef = useRef(0)
  const [streamingTick, setStreamingTick] = useState(0)

  const modelInfo: ModelOption | undefined = useMemo(
    () => MODELS.find((m) => m.id === model),
    [model],
  )

  const metrics: LiveMetrics = useMemo(() => {
    const { input: inputTokens, output: baseOutputTokens } = estimateTokens(messages)

    // Derive streaming tokens from the tick counter (not the ref) to avoid ref-during-render.
    // Each tick ≈ CHARS_PER_TICK chars ≈ CHARS_PER_TICK/4 tokens.
    const outputTokens = baseOutputTokens + streamingTick * Math.ceil(CHARS_PER_TICK / 4)

    const contextWindow = modelInfo?.contextWindow ?? 128_000
    const totalTokens = inputTokens + outputTokens
    const contextPct = contextWindow > 0 ? (totalTokens / contextWindow) * 100 : 0

    const sessionCost = modelInfo
      ? tokenCost(inputTokens, modelInfo.inputCostPer1M) + tokenCost(outputTokens, modelInfo.outputCostPer1M)
      : 0

    let messageCost = 0
    if (modelInfo && messages.length >= 2) {
      const lastAssistant = messages.findLast((m) => m.role === "assistant")
      const lastUser = messages.findLast((m) => m.role === "user")
      if (lastAssistant && lastUser) {
        const inTok = Math.ceil(lastUser.content.length / 4)
        const outTok = Math.ceil(lastAssistant.content.length / 4)
        messageCost = tokenCost(inTok, modelInfo.inputCostPer1M) + tokenCost(outTok, modelInfo.outputCostPer1M)
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
  }, [messages, modelInfo, streamingTick])

  const addStreamingChars = useCallback((chars: number) => {
    const prev = streamingCharsRef.current
    streamingCharsRef.current += chars
    // Only trigger re-render when estimated tokens cross next ~100-token boundary
    if (Math.floor(streamingCharsRef.current / CHARS_PER_TICK) > Math.floor(prev / CHARS_PER_TICK)) {
      setStreamingTick(Math.floor(streamingCharsRef.current / CHARS_PER_TICK))
    }
  }, [])

  const resetStreaming = useCallback(() => {
    streamingCharsRef.current = 0
    setStreamingTick(0)
  }, [])

  return { metrics, addStreamingChars, resetStreaming, modelInfo }
}