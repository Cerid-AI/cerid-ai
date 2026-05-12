// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Renders the chat message list with model-switch dividers.
 * Purely presentational — all state and callbacks are passed as props.
 */

import { useRef, useEffect, useState } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { RefreshCw } from "lucide-react"
import { cn } from "@/lib/utils"
import { FirstRunSuggestions } from "./first-run-suggestions"
import { MessageBubble, type MessageVerificationStatus } from "./message-bubble"
import { ModelSwitchDivider } from "./model-switch-divider"
import type { ChatMessage, HallucinationReport } from "@/lib/types"

/** Distance (px) from the bottom within which we still treat the viewport
 *  as "anchored to the latest message". Beyond this, we assume the user
 *  scrolled up to read history and stop auto-scrolling. */
const SCROLL_ANCHOR_THRESHOLD = 100

/** Format an epoch-ms timestamp as a coarse relative time. Granularity is
 *  intentionally low (no seconds) so the label is stable across renders
 *  while the user is reading. */
function formatRelativeTime(ts: number, now: number): string {
  const diffSec = Math.max(0, Math.floor((now - ts) / 1000))
  if (diffSec < 60) return "just now"
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin} min ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr} hr ago`
  const diffDay = Math.floor(diffHr / 24)
  if (diffDay < 7) return `${diffDay} day${diffDay === 1 ? "" : "s"} ago`
  return new Date(ts).toLocaleDateString()
}

/** Build a compact verification badge status from a stored report. */
function buildStatusFromReport(report: HallucinationReport): MessageVerificationStatus {
  // Guard: summary may be missing on reports from Neo4j persistence or malformed data
  const summary = report.summary
  if (!summary) return null
  if (report.skipped || summary.total === 0) return null
  return {
    state: "done",
    verified: summary.verified ?? 0,
    unverified: summary.unverified ?? 0,
    uncertain: summary.uncertain ?? 0,
    total: summary.total ?? 0,
  }
}

interface ChatMessagesProps {
  messages: ChatMessage[]
  isStreaming: boolean
  /** ID of the currently selected verification message (for full inline markup). */
  selectedVerificationMsgId: string | null
  verificationStatusForMsg: MessageVerificationStatus
  halReport: HallucinationReport | null
  inlineMarkups: boolean
  /** All stored verification reports keyed by message ID. */
  allVerificationReports: Record<string, HallucinationReport>
  onCorrect: (messageId: string, correction: string) => void
  onToggleMarkup?: () => void
  onClaimFocus?: (index: number) => void
  onArtifactClick: (artifactId: string) => void
  onSelectVerificationMsg?: (msgId: string | null) => void
  onEnrich?: (messageId: string, content: string) => void
  /** Called when user clicks "Try again" on a failed assistant message. Receives the preceding user message content. */
  onRetry?: (userContent: string) => void
  /** Re-run verification for the last assistant message. */
  onReVerify?: () => void
  /** Called when a first-run suggestion card is clicked. Parent wires this
   * to the chat input (populate text + optionally submit). */
  onPickSuggestion?: (prompt: string) => void
}

export function ChatMessages({
  messages,
  isStreaming,
  selectedVerificationMsgId,
  verificationStatusForMsg,
  halReport,
  inlineMarkups,
  allVerificationReports,
  onCorrect,
  onToggleMarkup,
  onClaimFocus,
  onArtifactClick,
  onSelectVerificationMsg,
  onEnrich,
  onRetry,
  onReVerify,
  onPickSuggestion,
}: ChatMessagesProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  // C-P0.2: scroll anchoring. When the user scrolls up to read history during
  // a stream, don't yank the viewport back to the bottom on every chunk.
  // Cleared when the user sends a new message (signalled by a fresh trailing
  // user message), or when the viewport scrolls back into the anchor zone.
  const userScrolledUpRef = useRef(false)

  // Tick relative-time labels every 30s so "just now" → "1 min ago" etc.
  // without rerendering every second.
  const [nowTick, setNowTick] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])

  // Track scroll-position vs. bottom so we can decide whether to auto-scroll.
  useEffect(() => {
    const viewport = scrollRef.current?.querySelector<HTMLDivElement>(
      "[data-radix-scroll-area-viewport]",
    )
    if (!viewport) return
    const handleScroll = () => {
      const distanceFromBottom =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
      userScrolledUpRef.current = distanceFromBottom > SCROLL_ANCHOR_THRESHOLD
    }
    viewport.addEventListener("scroll", handleScroll, { passive: true })
    return () => viewport.removeEventListener("scroll", handleScroll)
  }, [])

  // Auto-scroll on message changes, but only when anchored to the bottom.
  // A fresh trailing user message means the user just sent something — force
  // re-anchor in that case (their own send shouldn't be hidden).
  const lastMessage = messages[messages.length - 1]
  const lastIsUser = lastMessage?.role === "user"
  const lastMessageId = lastMessage?.id
  useEffect(() => {
    if (lastIsUser) {
      userScrolledUpRef.current = false
    }
  }, [lastIsUser, lastMessageId])

  useEffect(() => {
    if (userScrolledUpRef.current) return
    const viewport = scrollRef.current?.querySelector<HTMLDivElement>(
      "[data-radix-scroll-area-viewport]",
    )
    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight
    }
  }, [messages])

  return (
    <ScrollArea className="min-h-0 flex-1 px-4" ref={scrollRef}>
      <div className="mx-auto max-w-4xl py-4">
        {messages.length === 0 && (
          onPickSuggestion ? (
            <FirstRunSuggestions onPickSuggestion={onPickSuggestion} />
          ) : (
            <div className="flex items-center justify-center py-20 text-muted-foreground">
              <p>Start a conversation…</p>
            </div>
          )
        )}
        {messages.map((msg, i) => {
          let divider: React.ReactNode = null
          if (msg.role === "assistant" && msg.model) {
            const prevAssistant = messages
              .slice(0, i)
              .findLast((m) => m.role === "assistant" && m.model)
            if (prevAssistant?.model && prevAssistant.model !== msg.model) {
              divider = (
                <ModelSwitchDivider
                  key={`switch-${msg.id}`}
                  fromModelId={prevAssistant.model}
                  toModelId={msg.model}
                />
              )
            }
          }

          // Determine verification props for this message
          const isSelected = msg.id === selectedVerificationMsgId
          const storedReport = allVerificationReports[msg.id]
          const msgVerificationStatus = isSelected
            ? verificationStatusForMsg
            : storedReport
              ? buildStatusFromReport(storedReport)
              : undefined
          const msgClaims = isSelected && halReport?.claims ? halReport.claims : undefined
          const msgInlineMarkups = isSelected ? inlineMarkups : undefined

          // Click handler: any assistant message with a report can be selected
          const canSelectForVerification = msg.role === "assistant" && storedReport
          const handleBubbleClick = canSelectForVerification
            ? () => onSelectVerificationMsg?.(isSelected ? null : msg.id)  // Toggle: click again to deselect
            : undefined

          // Only the last non-streaming assistant message gets the re-verify button
          const isLastAssistant =
            msg.role === "assistant" &&
            !isStreaming &&
            messages.slice(i + 1).every((m) => m.role !== "assistant")

          // M-A.2: caret follows the actively-streaming assistant message — the
          // last assistant message while `isStreaming` is true.
          const isStreamingTarget =
            isStreaming &&
            msg.role === "assistant" &&
            messages.slice(i + 1).every((m) => m.role !== "assistant")

          // Detect failed assistant messages (error embedded in content)
          const isError =
            msg.role === "assistant" &&
            !isStreaming &&
            (msg.content.includes("**Error:**") || msg.content.startsWith("\u26A0"))
          const precedingUserMsg = isError
            ? messages.slice(0, i).findLast((m) => m.role === "user")
            : undefined

          return (
            <div
              key={msg.id}
              className={cn(
                "transition-all duration-300",
                isSelected && "rounded-xl ring-2 ring-brand/40 bg-brand/3 shadow-[0_0_16px_oklch(0.55_0.12_185/15%)]",
                canSelectForVerification && !isSelected && "cursor-pointer hover:bg-muted/20 rounded-xl",
              )}
              onClick={!isSelected ? handleBubbleClick : undefined}
            >
              {divider}
              <MessageBubble
                message={msg}
                verificationStatus={msgVerificationStatus}
                verificationClaims={msgClaims}
                inlineMarkups={msgInlineMarkups}
                isStreaming={isStreamingTarget}
                onCorrect={msg.role === "assistant" && !isStreaming ? onCorrect : undefined}
                onToggleMarkup={isSelected ? onToggleMarkup : undefined}
                onSelectForVerification={
                  canSelectForVerification
                    ? () => onSelectVerificationMsg?.(isSelected ? null : msg.id)
                    : undefined
                }
                onClaimFocus={isSelected ? onClaimFocus : undefined}
                onArtifactClick={msg.role === "assistant" ? onArtifactClick : undefined}
                onEnrich={msg.role === "assistant" && !isStreaming ? onEnrich : undefined}
                onReVerify={isLastAssistant ? onReVerify : undefined}
              />
              {isError && precedingUserMsg && onRetry && (
                <div className="flex items-center gap-2 px-12 pb-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 gap-1.5 text-xs text-muted-foreground"
                    onClick={(e) => {
                      e.stopPropagation()
                      onRetry(precedingUserMsg.content)
                    }}
                  >
                    <RefreshCw className="h-3 w-3" />
                    Try again
                  </Button>
                </div>
              )}
              {/* C-P1.2: render the message timestamp under each bubble.
                  Owned here rather than inside <MessageBubble> so Phase 4's
                  parallel agent doesn't need to merge bubble-internal changes. */}
              {msg.timestamp ? (
                <div
                  className={cn(
                    "flex px-4 pb-1",
                    msg.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  <time
                    dateTime={new Date(msg.timestamp).toISOString()}
                    title={new Date(msg.timestamp).toLocaleString()}
                    className="text-label-xxs text-muted-foreground/70 tabular-nums"
                  >
                    {formatRelativeTime(msg.timestamp, nowTick)}
                  </time>
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
    </ScrollArea>
  )
}
