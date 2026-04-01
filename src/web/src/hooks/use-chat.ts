// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useCallback } from "react"
import { streamChat, ingestFeedback, extractMemories } from "@/lib/api"
import { uuid } from "@/lib/utils"
import type { ChatMessage, SourceRef } from "@/lib/types"

interface UseChatOptions {
  onMessageStart: (convoId: string, message: ChatMessage) => void
  onMessageUpdate: (convoId: string, content: string) => void
  onModelResolved?: (convoId: string, model: string) => void
  feedbackEnabled?: boolean
  privateModeLevel?: number
}

export function useChat({ onMessageStart, onMessageUpdate, onModelResolved, feedbackEnabled, privateModeLevel = 0 }: UseChatOptions) {
  const [isStreaming, setIsStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const send = useCallback(
    async (convoId: string, messages: Pick<ChatMessage, "role" | "content">[], model: string, sourcesUsed?: SourceRef[]) => {
      setIsStreaming(true)
      abortRef.current = new AbortController()

      const assistantMsg: ChatMessage = {
        id: uuid(),
        role: "assistant",
        content: "",
        model,
        timestamp: Date.now(),
        sourcesUsed,
      }
      onMessageStart(convoId, assistantMsg)

      let accumulated = ""
      let aborted = false
      let resolvedModel = model

      // Throttle UI updates during streaming — flush every animation frame
      // for smooth rendering without overwhelming React's reconciler.
      let lastFlushTime = 0
      let rafId: number | null = null

      const flushUpdate = () => {
        lastFlushTime = Date.now()
        onMessageUpdate(convoId, accumulated)
      }

      try {
        await streamChat(messages, model, (chunk) => {
          accumulated += chunk
          const now = Date.now()
          if (now - lastFlushTime >= 16) {
            // Enough time passed (~60fps) — flush immediately
            if (rafId) { cancelAnimationFrame(rafId); rafId = null }
            flushUpdate()
          } else if (!rafId) {
            // Schedule flush on next animation frame
            rafId = requestAnimationFrame(() => {
              rafId = null
              flushUpdate()
            })
          }
        }, abortRef.current.signal, (info) => {
          resolvedModel = `openrouter/${info.resolved_model}`
          onModelResolved?.(convoId, resolvedModel)
        })
        // Flush any remaining throttled content
        if (rafId) { cancelAnimationFrame(rafId); rafId = null }
        flushUpdate()

        if (accumulated.length === 0) {
          accumulated = "\u26A0 No response received. Check your connection or try a different model."
          onMessageUpdate(convoId, accumulated)
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          aborted = true
        } else {
          const errorText = err instanceof Error ? err.message : "Unknown error"
          accumulated += accumulated ? `\n\n---\n**Error:** ${errorText}` : `**Error:** ${errorText}`
          onMessageUpdate(convoId, accumulated)
        }
      } finally {
        setIsStreaming(false)
        abortRef.current = null

        // Skip feedback + memory when private mode is active (level >= 1)
        if (privateModeLevel < 1) {
          if (feedbackEnabled && !aborted && accumulated.length > 100) {
            const lastUserMsg = [...messages].reverse().find((m) => m.role === "user")
            if (lastUserMsg) {
              ingestFeedback(lastUserMsg.content, accumulated, resolvedModel, convoId)
                .catch((err) => console.warn("[feedback-loop]", err))
            }
          }

          // Memory extraction: auto-trigger after 3+ user messages (≈6+ total messages)
          const userMessages = messages.filter((m) => m.role === "user")
          if (!aborted && accumulated.length > 100 && userMessages.length >= 3) {
            extractMemories(accumulated, convoId, resolvedModel)
              .catch((err) => console.warn("[memory-extract]", err))
          }
        }
      }
    },
    [onMessageStart, onMessageUpdate, onModelResolved, feedbackEnabled, privateModeLevel],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return { send, stop, isStreaming }
}