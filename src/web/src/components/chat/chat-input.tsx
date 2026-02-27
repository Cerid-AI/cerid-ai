// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useCallback, type KeyboardEvent } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Send, Square } from "lucide-react"

interface ChatInputProps {
  onSend: (content: string) => void
  onStop: () => void
  isStreaming: boolean
  disabled?: boolean
  injectedCount?: number
  onInputChange?: (text: string) => void
}

export function ChatInput({ onSend, onStop, isStreaming, disabled, injectedCount = 0, onInputChange }: ChatInputProps) {
  const [input, setInput] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed || isStreaming) return
    onSend(trimmed)
    setInput("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }
  }, [input, isStreaming, onSend])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend]
  )

  const handleInput = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [])

  return (
    <div className="flex items-end gap-2 border-t bg-background p-4">
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => {
          setInput(e.target.value)
          handleInput()
          onInputChange?.(e.target.value)
        }}
        onKeyDown={handleKeyDown}
        placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
        aria-label="Chat message input"
        rows={1}
        disabled={disabled || isStreaming}
        className="flex-1 resize-none rounded-lg border bg-muted/50 px-4 py-2.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      />
      {injectedCount > 0 && (
        <Badge variant="secondary" className="mb-1.5 text-xs">
          {injectedCount} source{injectedCount !== 1 ? "s" : ""}
        </Badge>
      )}
      {isStreaming ? (
        <Button variant="destructive" size="icon" aria-label="Stop generation" onClick={onStop}>
          <Square className="h-4 w-4" />
        </Button>
      ) : (
        <Button size="icon" aria-label="Send message" onClick={handleSend} disabled={!input.trim() || disabled}>
          <Send className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}