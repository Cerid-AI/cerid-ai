// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Renders the chat message list with model-switch dividers.
 * Purely presentational — all state and callbacks are passed as props.
 */

import { useRef, useEffect } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MessageBubble, type MessageVerificationStatus } from "./message-bubble"
import { ModelSwitchDivider } from "./model-switch-divider"
import type { ChatMessage, HallucinationReport } from "@/lib/types"

interface ChatMessagesProps {
  messages: ChatMessage[]
  isStreaming: boolean
  /** ID of the latest assistant message (for attaching verification data). */
  lastAssistantMsgId: string | null
  verificationStatusForMsg: MessageVerificationStatus
  halReport: HallucinationReport | null
  hallucinationEnabled: boolean
  inlineMarkups: boolean
  onCorrect: (messageId: string, correction: string) => void
  onVerify: () => void
  onArtifactClick: (artifactId: string) => void
}

export function ChatMessages({
  messages,
  isStreaming,
  lastAssistantMsgId,
  verificationStatusForMsg,
  halReport,
  hallucinationEnabled,
  inlineMarkups,
  onCorrect,
  onVerify,
  onArtifactClick,
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
          return (
            <div key={msg.id}>
              {divider}
              <MessageBubble
                message={msg}
                verificationStatus={msg.id === lastAssistantMsgId ? verificationStatusForMsg : undefined}
                verificationClaims={msg.id === lastAssistantMsgId && halReport?.claims ? halReport.claims : undefined}
                inlineMarkups={msg.id === lastAssistantMsgId ? inlineMarkups : undefined}
                onCorrect={msg.role === "assistant" && !isStreaming ? onCorrect : undefined}
                onVerify={msg.role === "assistant" && !isStreaming && hallucinationEnabled && msg.id === lastAssistantMsgId ? onVerify : undefined}
                onArtifactClick={msg.role === "assistant" ? onArtifactClick : undefined}
              />
            </div>
          )
        })}
      </div>
    </ScrollArea>
  )
}
