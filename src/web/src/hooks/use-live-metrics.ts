import { useState, useCallback, useMemo } from "react"
import { MODELS } from "@/lib/types"
import type { ChatMessage, LiveMetrics, ModelOption } from "@/lib/types"

/**
 * Tracks token usage, context window consumption, and cost estimates
 * in real-time as chat messages stream in.
 */
export function useLiveMetrics(model: string, messages: ChatMessage[]) {
  const [streamingOutputChars, setStreamingOutputChars] = useState(0)

  const modelInfo: ModelOption | undefined = useMemo(
    () => MODELS.find((m) => m.id === model),
    [model],
  )

  const metrics: LiveMetrics = useMemo(() => {
    let inputTokens = 0
    let outputTokens = 0

    for (const msg of messages) {
      const est = Math.ceil(msg.content.length / 4)
      if (msg.role === "assistant") {
        outputTokens += est
      } else {
        inputTokens += est
      }
    }

    // Add streaming output that hasn't been committed to messages yet
    outputTokens += Math.ceil(streamingOutputChars / 4)

    const contextWindow = modelInfo?.contextWindow ?? 128_000
    const totalTokens = inputTokens + outputTokens
    const contextPct = contextWindow > 0 ? (totalTokens / contextWindow) * 100 : 0

    const sessionCost = modelInfo
      ? (inputTokens * modelInfo.inputCostPer1M + outputTokens * modelInfo.outputCostPer1M) / 1_000_000
      : 0

    // Cost of the last message pair (last user + last assistant)
    let messageCost = 0
    if (modelInfo && messages.length >= 2) {
      const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant")
      const lastUser = [...messages].reverse().find((m) => m.role === "user")
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

  // Call during streaming to track incremental output
  const addStreamingChars = useCallback((chars: number) => {
    setStreamingOutputChars((prev) => prev + chars)
  }, [])

  // Reset streaming counter when a message completes
  const resetStreaming = useCallback(() => {
    setStreamingOutputChars(0)
  }, [])

  return { metrics, addStreamingChars, resetStreaming, modelInfo }
}
