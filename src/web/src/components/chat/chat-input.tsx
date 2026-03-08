// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useCallback, type KeyboardEvent } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Send, Square } from "lucide-react"
import { DomainBadge } from "@/components/ui/domain-badge"
import { useDragDrop } from "@/hooks/use-drag-drop"
import { cn } from "@/lib/utils"

interface InjectedSource {
  filename: string
  domain: string
  content: string
}

interface ChatInputProps {
  onSend: (content: string) => void
  onStop: () => void
  isStreaming: boolean
  disabled?: boolean
  injectedCount?: number
  injectedSources?: InjectedSource[]
  onInputChange?: (text: string) => void
  onFileDrop?: (files: File[]) => void
}

export function ChatInput({ onSend, onStop, isStreaming, disabled, injectedCount = 0, injectedSources, onInputChange, onFileDrop }: ChatInputProps) {
  const [input, setInput] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleFiles = useCallback((files: File[]) => onFileDrop?.(files), [onFileDrop])
  const { isDragOver, dragHandlers } = useDragDrop(handleFiles)

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
    <div
      className={cn("flex items-end gap-2 border-t bg-background p-4", isDragOver && "ring-2 ring-primary ring-inset")}
      {...dragHandlers}
    >
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
        <Popover>
          <PopoverTrigger asChild>
            <Badge variant="secondary" className="mb-1.5 cursor-pointer text-xs hover:bg-accent">
              {injectedCount} source{injectedCount !== 1 ? "s" : ""}
            </Badge>
          </PopoverTrigger>
          <PopoverContent className="w-72 p-2" align="end">
            <p className="mb-2 text-xs font-medium text-muted-foreground">Injected context</p>
            <div className="space-y-2">
              {injectedSources?.map((src, i) => (
                <div key={i} className="rounded border p-2">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-xs font-medium">{src.filename}</span>
                    <DomainBadge domain={src.domain} />
                  </div>
                  <p className="mt-1 line-clamp-2 text-[10px] text-muted-foreground">
                    {src.content.slice(0, 120)}{src.content.length > 120 ? "..." : ""}
                  </p>
                </div>
              ))}
              {(!injectedSources || injectedSources.length === 0) && (
                <p className="text-xs text-muted-foreground">{injectedCount} source{injectedCount !== 1 ? "s" : ""} ready</p>
              )}
            </div>
          </PopoverContent>
        </Popover>
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