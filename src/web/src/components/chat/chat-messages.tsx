// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Renders the chat message list with model-switch dividers.
 * Purely presentational — all state and callbacks are passed as props.
 */

import { useRef, useEffect } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import { MessageBubble, type MessageVerificationStatus } from "./message-bubble"
import { ModelSwitchDivider } from "./model-switch-divider"
import type { ChatMessage, HallucinationReport } from "@/lib/types"

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
}: ChatMessagesProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
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
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <p>Start a conversation...</p>
          </div>
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
                onCorrect={msg.role === "assistant" && !isStreaming ? onCorrect : undefined}
                onToggleMarkup={isSelected ? onToggleMarkup : undefined}
                onSelectForVerification={
                  canSelectForVerification
                    ? () => onSelectVerificationMsg?.(isSelected ? null : msg.id)
                    : undefined
                }
                onClaimFocus={isSelected ? onClaimFocus : undefined}
                onArtifactClick={msg.role === "assistant" ? onArtifactClick : undefined}
              />
            </div>
          )
        })}
      </div>
    </ScrollArea>
  )
}
