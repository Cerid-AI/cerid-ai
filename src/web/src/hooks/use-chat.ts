import { useState, useRef, useCallback } from "react"
import { streamChat } from "@/lib/api"
import type { ChatMessage } from "@/lib/types"

interface UseChatOptions {
  onMessageStart: (convoId: string, message: ChatMessage) => void
  onMessageUpdate: (convoId: string, content: string) => void
}

export function useChat({ onMessageStart, onMessageUpdate }: UseChatOptions) {
  const [isStreaming, setIsStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const send = useCallback(
    async (convoId: string, messages: Pick<ChatMessage, "role" | "content">[], model: string) => {
      setIsStreaming(true)
      abortRef.current = new AbortController()

      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        model,
        timestamp: Date.now(),
      }
      onMessageStart(convoId, assistantMsg)

      let accumulated = ""

      try {
        for await (const chunk of streamChat(messages, model, abortRef.current.signal)) {
          accumulated += chunk
          onMessageUpdate(convoId, accumulated)
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // User cancelled — keep partial content
        } else {
          const errorText = err instanceof Error ? err.message : "Unknown error"
          accumulated += accumulated ? `\n\n---\n**Error:** ${errorText}` : `**Error:** ${errorText}`
          onMessageUpdate(convoId, accumulated)
        }
      } finally {
        setIsStreaming(false)
        abortRef.current = null
      }
    },
    [onMessageStart, onMessageUpdate]
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return { send, stop, isStreaming }
}
