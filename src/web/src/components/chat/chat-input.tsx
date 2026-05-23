// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useRef, useCallback, type KeyboardEvent, type DragEvent } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Send, Square } from "lucide-react"
import { DomainBadge } from "@/components/ui/domain-badge"
import { useDragDrop } from "@/hooks/use-drag-drop"
import { cn } from "@/lib/utils"
import type { KBQueryResult } from "@/lib/types"
import { logSwallowedError } from "@/lib/log-swallowed"
import { useNavigation } from "@/contexts/navigation-context"
import { KnowledgeSourceSelector, type KnowledgeSource } from "./knowledge-source-selector"

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
  onArtifactDrop?: (artifact: KBQueryResult) => void
}

export function ChatInput({ onSend, onStop, isStreaming, disabled, injectedCount = 0, injectedSources, onInputChange, onFileDrop, onArtifactDrop }: ChatInputProps) {
  const [input, setInput] = useState("")
  const [knowledgeSource, setKnowledgeSource] = useState<KnowledgeSource>(() => {
    try {
      const stored = localStorage.getItem("cerid-knowledge-source")
      if (stored === "kb" || stored === "kb_web" || stored === "llm_kb") return stored
    } catch { /* SSR */ }
    return "llm_kb"
  })
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [isArtifactDragOver, setIsArtifactDragOver] = useState(false)
  // Last-sent user message for ArrowUp recall (C-P1.6)
  const lastSentRef = useRef<string>("")
  const { activePane, consumeChatSeed } = useNavigation()
  // Cross-pane seeds (e.g. "Ask about this community" from the Communities
  // pane) land here when the user lands on the chat pane. Consume once,
  // focus the textarea, leave editable so the user can refine before sending.
  useEffect(() => {
    if (activePane !== "chat") return
    const seed = consumeChatSeed()
    if (!seed) return
    setInput(seed.text)
    onInputChange?.(seed.text)
    queueMicrotask(() => {
      const ta = textareaRef.current
      if (ta) {
        ta.focus()
        ta.setSelectionRange(seed.text.length, seed.text.length)
      }
    })
  }, [activePane, consumeChatSeed, onInputChange])
  // Synchronous guard against rapid-Enter double-submit.
  // `isStreaming` is React state and won't update between the Enter-press and state propagation,
  // so a second Enter fired before the parent flips `isStreaming` to true would double-send.
  const sendingRef = useRef(false)

  const handleFiles = useCallback((files: File[]) => onFileDrop?.(files), [onFileDrop])
  const { isDragOver, dragHandlers } = useDragDrop(handleFiles)

  // Artifact drag-drop handlers (KB cards dragged to chat input)
  const handleArtifactDragOver = useCallback((e: DragEvent) => {
    if (e.dataTransfer.types.includes("application/cerid-artifact")) {
      e.preventDefault()
      e.dataTransfer.dropEffect = "copy"
      setIsArtifactDragOver(true)
    }
  }, [])

  const handleArtifactDrop = useCallback((e: DragEvent) => {
    const data = e.dataTransfer.getData("application/cerid-artifact")
    if (data) {
      e.preventDefault()
      e.stopPropagation()
      setIsArtifactDragOver(false)
      try {
        const artifact = JSON.parse(data) as KBQueryResult
        onArtifactDrop?.(artifact)
      } catch (err) { logSwallowedError(err, "json.parse.artifact-drop") }
    }
  }, [onArtifactDrop])

  const handleArtifactDragLeave = useCallback(() => {
    setIsArtifactDragOver(false)
  }, [])

  const handleSend = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed || isStreaming) return
    sendingRef.current = true
    try {
      lastSentRef.current = trimmed
      onSend(trimmed)
      setInput("")
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto"
      }
    } finally {
      // Released on the next tick so a second Enter fired in the same event burst
      // still sees the guard. The parent's `isStreaming` will have flipped by then.
      queueMicrotask(() => {
        sendingRef.current = false
      })
    }
  }, [input, isStreaming, onSend])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        if (sendingRef.current) return
        handleSend()
        return
      }
      // C-P0.1: Esc cancels an in-flight streaming response. Keyboard escape
      // hatch for users who can't reach the stop button by mouse.
      if (e.key === "Escape" && isStreaming) {
        e.preventDefault()
        onStop()
        return
      }
      // C-P1.6: ArrowUp on an empty composer restores the last-sent user
      // message — common chat ergonomic (Slack, Discord, iMessage).
      if (e.key === "ArrowUp" && input.length === 0 && lastSentRef.current) {
        e.preventDefault()
        const recalled = lastSentRef.current
        setInput(recalled)
        onInputChange?.(recalled)
        // Defer caret-to-end + autoresize until after React paints the new value
        queueMicrotask(() => {
          const ta = textareaRef.current
          if (ta) {
            ta.setSelectionRange(recalled.length, recalled.length)
            ta.style.height = "auto"
            ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
          }
        })
      }
    },
    [handleSend, isStreaming, onStop, input.length, onInputChange]
  )

  const handleInput = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [])

  return (
    <div
      className={cn(
        "relative flex items-end gap-2 border-t bg-background p-4",
        isDragOver && "ring-2 ring-primary ring-inset",
        isArtifactDragOver && "ring-2 ring-brand ring-inset bg-brand/5",
      )}
      {...dragHandlers}
      onDragOver={(e) => { dragHandlers.onDragOver?.(e); handleArtifactDragOver(e) }}
      onDrop={(e) => { handleArtifactDrop(e); dragHandlers.onDrop?.(e) }}
      onDragLeave={(e) => { dragHandlers.onDragLeave?.(e); handleArtifactDragLeave() }}
    >
      {/* C-P2.1: explicit drag-over overlay labels — colour-only signalling is too subtle. */}
      {(isDragOver || isArtifactDragOver) && (
        <div
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-md text-sm font-medium",
            isArtifactDragOver ? "bg-brand/10 text-brand" : "bg-primary/10 text-primary",
          )}
        >
          {isArtifactDragOver ? "Drop artifact to inject" : "Drop file to attach"}
        </div>
      )}
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => {
          // Block edits while streaming — read-only behaviour without losing
          // keyboard focus (so Escape-to-cancel still works). Native `readOnly`
          // would also disable our paste handling for drag-drop helpers.
          if (isStreaming) return
          setInput(e.target.value)
          handleInput()
          onInputChange?.(e.target.value)
        }}
        onKeyDown={handleKeyDown}
        placeholder={
          isStreaming
            ? "Streaming — Esc to cancel"
            : "Type a message... (Enter to send, Shift+Enter for new line)"
        }
        aria-label="Chat message input"
        aria-readonly={isStreaming || undefined}
        rows={1}
        disabled={disabled}
        className="flex-1 resize-none rounded-lg border bg-muted/50 px-4 py-2.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      />
      <KnowledgeSourceSelector
        value={knowledgeSource}
        onChange={(next) => {
          setKnowledgeSource(next)
          try { localStorage.setItem("cerid-knowledge-source", next) } catch { /* SSR */ }
        }}
      />
      {injectedCount > 0 && (
        <Popover>
          <TooltipProvider delayDuration={300}>
            <Tooltip>
              <TooltipTrigger asChild>
                <PopoverTrigger asChild>
                  <Badge variant="secondary" className="mb-1.5 cursor-pointer text-xs hover:bg-accent">
                    {injectedCount} source{injectedCount !== 1 ? "s" : ""}
                  </Badge>
                </PopoverTrigger>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs">
                <p className="text-xs">
                  {injectedSources && injectedSources.length > 0
                    ? `Injecting: ${injectedSources.map((s) => s.filename).join(", ")}`
                    : `${injectedCount} source${injectedCount !== 1 ? "s" : ""} ready`}
                </p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <PopoverContent className="w-72 p-2" align="end">
            <p className="mb-2 text-xs font-medium text-muted-foreground">Injected context</p>
            <div className="space-y-2">
              {injectedSources?.map((src, i) => (
                <div key={i} className="rounded border p-2">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-xs font-medium">{src.filename}</span>
                    <DomainBadge domain={src.domain} />
                  </div>
                  <p className="mt-1 line-clamp-2 text-label-xs text-muted-foreground">
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