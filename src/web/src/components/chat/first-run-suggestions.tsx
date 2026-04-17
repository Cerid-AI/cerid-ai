// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FirstRunSuggestions — empty-chat welcome with 3 clickable prompt cards.
 *
 * Replaces the terse "Start a conversation..." that greeted new users with
 * actionable suggestions they can click to populate the chat input. Each
 * card is a safe "what does this product do?" question that works before
 * any KB is ingested, so the first query isn't a confused stab in the dark.
 */
import { BookOpen, MessageSquareText, ShieldCheck } from "lucide-react"
import type { LucideIcon } from "lucide-react"

import { Card, CardContent } from "@/components/ui/card"

interface SuggestionCard {
  icon: LucideIcon
  title: string
  prompt: string
}

const SUGGESTIONS: SuggestionCard[] = [
  {
    icon: MessageSquareText,
    title: "What can you help with?",
    prompt:
      "I'm new to Cerid AI. What can you help me with, and what's the best way to start?",
  },
  {
    icon: BookOpen,
    title: "How does your knowledge base work?",
    prompt:
      "In simple terms: how does your RAG / knowledge base work, and when does it improve my answers?",
  },
  {
    icon: ShieldCheck,
    title: "Show me response verification",
    prompt:
      "Give me an example factual claim and walk me through how you'd verify it against external sources.",
  },
]

interface FirstRunSuggestionsProps {
  /** Called with the prompt text when a card is clicked. Parent wires this
   * to the chat input (setter + optional send-on-click). */
  onPickSuggestion: (prompt: string) => void
}

export function FirstRunSuggestions({ onPickSuggestion }: FirstRunSuggestionsProps) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-center gap-6 py-16 text-center">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold tracking-tight">Welcome to Cerid AI</h2>
        <p className="max-w-md text-sm text-muted-foreground">
          Type anything below, or start with one of these:
        </p>
      </div>
      <div className="grid w-full grid-cols-1 gap-3 md:grid-cols-3">
        {SUGGESTIONS.map((s) => {
          const Icon = s.icon
          return (
            <Card
              key={s.title}
              role="button"
              tabIndex={0}
              onClick={() => onPickSuggestion(s.prompt)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault()
                  onPickSuggestion(s.prompt)
                }
              }}
              className="cursor-pointer border border-border/60 transition hover:border-brand/40 hover:bg-brand/5 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <CardContent className="space-y-2 p-4 text-left">
                <Icon className="h-5 w-5 text-brand/80" aria-hidden="true" />
                <p className="text-sm font-medium">{s.title}</p>
                <p className="text-xs text-muted-foreground line-clamp-2">
                  {s.prompt}
                </p>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
