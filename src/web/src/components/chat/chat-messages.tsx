// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Renders the chat message list with model-switch dividers.
 * Purely presentational — all state and callbacks are passed as props.
 *
 * Two implementations live behind the same public API:
 *
 *   - ``PlainChatMessages`` (default): plain ``.map()`` over the list.
 *     Cheap at small N, cheap to debug.
 *   - ``VirtualizedChatMessages`` (v0.93.5 / opt-in via
 *     ``useChatVirtualization()``): ``@tanstack/react-virtual`` keeps
 *     only the visible window in DOM.  Recommended once a conversation
 *     crosses ~200 messages.
 *
 * The dispatcher at the bottom picks based on the flag; both branches
 * share the same ``MessageRow`` so render parity is enforced
 * structurally rather than by convention.
 */

import { useRef, useEffect, useState, useMemo } from "react"
import { useVirtualizer, type VirtualItem } from "@tanstack/react-virtual"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { RefreshCw } from "lucide-react"
import { cn } from "@/lib/utils"
import { FirstRunSuggestions } from "./first-run-suggestions"
import { MessageBubble, type MessageVerificationStatus } from "./message-bubble"
import { ModelSwitchDivider } from "./model-switch-divider"
import { useChatVirtualization } from "@/hooks/use-chat-virtualization"
import type { ChatMessage, HallucinationReport } from "@/lib/types"

/** Distance (px) from the bottom within which we still treat the viewport
 *  as "anchored to the latest message". Beyond this, we assume the user
 *  scrolled up to read history and stop auto-scrolling. */
const SCROLL_ANCHOR_THRESHOLD = 100

/** Estimated per-message height (px) used as the virtualizer's seed.  The
 *  virtualizer re-measures actual heights via ``measureElement`` once
 *  rendered, so this is a hint, not a hard budget. */
const ESTIMATED_ROW_HEIGHT = 120

/** Number of items to render outside the visible window on each side.
 *  Higher = smoother scroll but more DOM cost; 5 is the
 *  ``@tanstack/react-virtual`` recommended default for streaming chat. */
const OVERSCAN = 5

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
  /** Active conversation id — threaded to per-message thumbs feedback (CR-043). */
  conversationId: string
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
  /** Called when a first-run suggestion card is clicked. */
  onPickSuggestion?: (prompt: string) => void
}

interface MessageRowProps extends Omit<ChatMessagesProps, "messages" | "onPickSuggestion"> {
  msg: ChatMessage
  i: number
  messages: ChatMessage[]
  nowTick: number
}

/**
 * Single message row — extracted so both the plain ``.map()`` and the
 * virtualized branch render structurally identical output.  When a
 * future change touches per-message styling, neither branch is allowed
 * to drift; the diff lands here once.
 */
function MessageRow({
  msg,
  i,
  messages,
  conversationId,
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
  nowTick,
}: MessageRowProps) {
  let divider: React.ReactNode = null
  if (msg.role === "assistant" && msg.model) {
    const prevAssistant = messages
      .slice(0, i)
      .findLast((m) => m.role === "assistant" && m.model)
    if (prevAssistant?.model && prevAssistant.model !== msg.model) {
      divider = (
        <ModelSwitchDivider
          fromModelId={prevAssistant.model}
          toModelId={msg.model}
        />
      )
    }
  }

  const isSelected = msg.id === selectedVerificationMsgId
  const storedReport = allVerificationReports[msg.id]
  const msgVerificationStatus = isSelected
    ? verificationStatusForMsg
    : storedReport
      ? buildStatusFromReport(storedReport)
      : undefined
  const msgClaims = isSelected && halReport?.claims ? halReport.claims : undefined
  const msgInlineMarkups = isSelected ? inlineMarkups : undefined

  const canSelectForVerification = msg.role === "assistant" && storedReport
  const handleBubbleClick = canSelectForVerification
    ? () => onSelectVerificationMsg?.(isSelected ? null : msg.id)
    : undefined

  const isLastAssistant =
    msg.role === "assistant" &&
    !isStreaming &&
    messages.slice(i + 1).every((m) => m.role !== "assistant")

  const isStreamingTarget =
    isStreaming &&
    msg.role === "assistant" &&
    messages.slice(i + 1).every((m) => m.role !== "assistant")

  const isError =
    msg.role === "assistant" &&
    !isStreaming &&
    (msg.content.includes("**Error:**") || msg.content.startsWith("⚠"))
  const precedingUserMsg = isError
    ? messages.slice(0, i).findLast((m) => m.role === "user")
    : undefined

  // When the message is clickable for verification-selection it becomes
  // an interactive surface; otherwise role="presentation" keeps a11y
  // tooling from treating the static bubble as actionable.
  const isInteractive = canSelectForVerification && !isSelected
  return (
    <div
      role={isInteractive ? "button" : "presentation"}
      tabIndex={isInteractive ? 0 : undefined}
      aria-label={isInteractive ? "Select message for verification" : undefined}
      className={cn(
        "transition-all duration-300",
        isSelected && "rounded-xl ring-2 ring-brand/40 bg-brand/3 shadow-[0_0_16px_oklch(0.55_0.12_185/15%)]",
        canSelectForVerification && !isSelected && "cursor-pointer hover:bg-muted/20 rounded-xl",
      )}
      onClick={isInteractive && handleBubbleClick ? handleBubbleClick : undefined}
      onKeyDown={
        isInteractive && handleBubbleClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                handleBubbleClick()
              }
            }
          : undefined
      }
    >
      {divider}
      <MessageBubble
        message={msg}
        conversationId={conversationId}
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
}

/**
 * Plain ``.map()`` implementation — the default through v0.93.5.
 */
function PlainChatMessages(props: ChatMessagesProps) {
  const { messages, onPickSuggestion } = props
  const scrollRef = useRef<HTMLDivElement>(null)
  const userScrolledUpRef = useRef(false)

  const [nowTick, setNowTick] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])

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
    <ScrollArea className="min-h-0 flex-1 px-2 md:px-4" ref={scrollRef}>
      <div className="mx-auto max-w-none py-4 md:max-w-4xl">
        {messages.length === 0 && (
          onPickSuggestion ? (
            <FirstRunSuggestions onPickSuggestion={onPickSuggestion} />
          ) : (
            <div className="flex items-center justify-center py-20 text-muted-foreground">
              <p>Start a conversation…</p>
            </div>
          )
        )}
        {messages.map((msg, i) => (
          <MessageRow
            key={msg.id}
            {...props}
            msg={msg}
            i={i}
            messages={messages}
            nowTick={nowTick}
          />
        ))}
      </div>
    </ScrollArea>
  )
}

/**
 * Virtualized implementation (v0.93.5 / Cycle 3.2 follow-on).
 *
 * Renders only the visible window via ``@tanstack/react-virtual``.  The
 * scroll-anchor heuristic switches from ``viewport.scrollHeight`` (which
 * is unreliable under virtualization) to comparing the last virtual
 * index against the total count — same semantics, anchor-aware
 * implementation.
 */
function VirtualizedChatMessages(props: ChatMessagesProps) {
  const { messages, onPickSuggestion } = props
  const scrollRef = useRef<HTMLDivElement>(null)
  const userScrolledUpRef = useRef(false)
  // Tracked as state so the virtualizer's getScrollElement() picks up
  // the viewport after Radix's portal-style inner div mounts.  Using a
  // plain ref would leave the virtualizer with null on first render and
  // never re-measure.
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null)

  const [nowTick, setNowTick] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])

  // Resolve the Radix ScrollArea viewport once the wrapper mounts.
  // Setting it into state triggers the virtualizer to re-measure
  // against the real scroll element.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally omitted dep; addition would cause infinite loop or unwanted re-fetch
  useEffect(() => {
    const el = scrollRef.current?.querySelector<HTMLDivElement>(
      "[data-radix-scroll-area-viewport]",
    ) ?? null
    if (el && el !== scrollEl) setScrollEl(el)
  })

  // eslint-disable-next-line react-hooks/incompatible-library -- third-party API not React-Compiler-compatible (ResizeObserver / IntersectionObserver / similar)
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollEl,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: OVERSCAN,
    // ``measureElement`` is what makes ``data-index`` markers stable
    // under jsdom (per the polyfill in src/__tests__/setup.ts) and
    // gives the virtualizer real heights at runtime.
    measureElement:
      typeof window !== "undefined" && typeof Element !== "undefined"
        ? (el) => el.getBoundingClientRect().height
        : undefined,
  })

  // Anchor-tracking: a user is "scrolled up" when the last visible
  // virtual item is more than ~5 rows away from the end of the list.
  // This replaces the scrollHeight-based pixel math from the plain
  // implementation, which is unreliable when items are virtualized.
  useEffect(() => {
    if (!scrollEl) return
    const handleScroll = () => {
      const items = virtualizer.getVirtualItems()
      if (items.length === 0) return
      const lastVisible = items[items.length - 1]
      const remaining = messages.length - 1 - lastVisible.index
      userScrolledUpRef.current = remaining > OVERSCAN
    }
    scrollEl.addEventListener("scroll", handleScroll, { passive: true })
    return () => scrollEl.removeEventListener("scroll", handleScroll)
  }, [scrollEl, virtualizer, messages.length])

  // When the user just sent a message, force re-anchor (their own send
  // should never be hidden).  Matches the plain implementation.
  const lastMessage = messages[messages.length - 1]
  const lastIsUser = lastMessage?.role === "user"
  const lastMessageId = lastMessage?.id
  useEffect(() => {
    if (lastIsUser) {
      userScrolledUpRef.current = false
    }
  }, [lastIsUser, lastMessageId])

  // Auto-scroll on message changes when anchored.  Uses the
  // virtualizer's index-based API instead of pixel math.
  useEffect(() => {
    if (userScrolledUpRef.current) return
    if (messages.length === 0) return
    virtualizer.scrollToIndex(messages.length - 1, { align: "end" })
  }, [messages, virtualizer])

  // Re-measure when a streaming message grows.  The virtualizer's own
  // measure-on-render covers most cases, but explicit re-measure of
  // the last item keeps the anchor calculation in sync as new tokens
  // arrive.
  useEffect(() => {
    if (!props.isStreaming || messages.length === 0) return
    virtualizer.measure()
  }, [props.isStreaming, lastMessageId, virtualizer, messages.length])

  const virtualItems = virtualizer.getVirtualItems()
  const totalSize = virtualizer.getTotalSize()

  // Pre-compute the empty-state branch so the virtualizer code path
  // doesn't have to fight to render zero items.
  if (messages.length === 0) {
    return (
      <ScrollArea className="min-h-0 flex-1 px-2 md:px-4" ref={scrollRef}>
        <div className="mx-auto max-w-none py-4 md:max-w-4xl">
          {onPickSuggestion ? (
            <FirstRunSuggestions onPickSuggestion={onPickSuggestion} />
          ) : (
            <div className="flex items-center justify-center py-20 text-muted-foreground">
              <p>Start a conversation…</p>
            </div>
          )}
        </div>
      </ScrollArea>
    )
  }

  return (
    <ScrollArea className="min-h-0 flex-1 px-2 md:px-4" ref={scrollRef}>
      <div className="mx-auto max-w-none py-4 md:max-w-4xl">
        <div
          style={{ // drift-allowed: TanStack Virtual requires per-item height/position from live measurement
            height: `${totalSize}px`,
            position: "relative",
            width: "100%",
          }}
        >
          {virtualItems.map((vi: VirtualItem) => {
            const msg = messages[vi.index]
            return (
              <div
                key={msg.id}
                data-index={vi.index}
                ref={virtualizer.measureElement}
                style={{ // drift-allowed: TanStack Virtual requires per-item height/position from live measurement
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${vi.start}px)`,
                }}
              >
                <MessageRow
                  {...props}
                  msg={msg}
                  i={vi.index}
                  messages={messages}
                  nowTick={nowTick}
                />
              </div>
            )
          })}
        </div>
      </div>
    </ScrollArea>
  )
}

/**
 * Public dispatcher.  Picks the implementation once per mount based on
 * the feature flag; toggling the flag mid-conversation requires a
 * reload (see ``useChatVirtualization`` for why).
 */
export function ChatMessages(props: ChatMessagesProps) {
  const virtualized = useChatVirtualization()
  // useMemo on the choice itself so React doesn't unmount the plain
  // implementation if the hook's storage-event listener happens to
  // re-run (the value should be stable for the conversation lifetime).
  const Component = useMemo(
    () => (virtualized ? VirtualizedChatMessages : PlainChatMessages),
    [virtualized],
  )
  return <Component {...props} />
}
