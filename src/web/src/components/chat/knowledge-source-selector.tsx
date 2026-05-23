// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Knowledge-source selector chip for the chat composer — Phase C
// Day 4. Three modes per design-system-v2 §9.1:
//   - kb       (🧠)  : Local knowledge only — private, no LLM general training
//   - kb+web   (🧠+🌐): Local KB + web search augmentation
//   - llm+kb   (🤖+🧠): LLM general knowledge grounded by local KB (default)
//
// State lives in the chat composer; this component is a presentational
// chip. Backend wiring to the chat agent (so the chosen mode actually
// changes retrieval behavior) lands incrementally — Phase C ships the
// UI primitive; subsequent commits wire it through chat/query routes.

import { useState } from "react"
import { Brain, Globe, Bot, ChevronDown } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

export type KnowledgeSource = "kb" | "kb_web" | "llm_kb"

interface KnowledgeSourceMeta {
  id: KnowledgeSource
  label: string
  short: string
  description: string
  glow: string  // tailwind shadow color for the brand "ambient glow"
}

const SOURCES: KnowledgeSourceMeta[] = [
  {
    id: "kb",
    label: "Local KB only",
    short: "🧠",
    description: "Answers grounded only in your local corpus. No external LLM general knowledge, no web search. Best for private, high-confidence factual recall.",
    glow: "shadow-[0_0_8px_2px_rgba(90,236,203,0.35)]",  // brand teal
  },
  {
    id: "kb_web",
    label: "Local KB + Web",
    short: "🧠+🌐",
    description: "Local corpus first, then external web search to fill gaps. Cited results from both sources.",
    glow: "shadow-[0_0_8px_2px_rgba(122,200,229,0.35)]",  // mentions-edge blue
  },
  {
    id: "llm_kb",
    label: "LLM + KB grounding",
    short: "🤖+🧠",
    description: "LLM general knowledge with local KB used as ground truth. Best for analysis, synthesis, and questions outside your corpus.",
    glow: "shadow-[0_0_8px_2px_rgba(212,175,55,0.35)]",  // brand gold
  },
]

const SOURCE_BY_ID: Record<KnowledgeSource, KnowledgeSourceMeta> = Object.fromEntries(
  SOURCES.map((s) => [s.id, s]),
) as Record<KnowledgeSource, KnowledgeSourceMeta>

export interface KnowledgeSourceSelectorProps {
  value: KnowledgeSource
  onChange: (next: KnowledgeSource) => void
}

export function KnowledgeSourceSelector({ value, onChange }: KnowledgeSourceSelectorProps) {
  const [open, setOpen] = useState(false)
  const current = SOURCE_BY_ID[value]

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Knowledge source: ${current.label}`}
          title={current.label}
          className={`flex h-7 items-center gap-1 rounded-full border border-border bg-card/60 px-2 text-label-xs text-foreground/85 transition-shadow hover:bg-accent/40 ${current.glow}`}
        >
          <span aria-hidden="true">{current.short}</span>
          <ChevronDown className="h-3 w-3 opacity-60" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-1" align="end" side="top">
        <div className="px-2 pb-1 pt-2 text-label-xs uppercase tracking-wide text-muted-foreground">
          Knowledge source
        </div>
        <ul role="listbox" aria-label="Knowledge source modes" className="flex flex-col gap-0.5">
          {SOURCES.map((src) => {
            const isActive = src.id === value
            return (
              <li key={src.id} role="option" aria-selected={isActive}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(src.id)
                    setOpen(false)
                  }}
                  className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${
                    isActive ? "bg-accent text-accent-foreground" : "text-foreground/80 hover:bg-accent/40"
                  }`}
                >
                  <span className="mt-0.5 text-base leading-none" aria-hidden="true">
                    {src.short}
                  </span>
                  <span className="flex-1">
                    <span className="block text-sm font-medium">{src.label}</span>
                    <span className="block text-label-xs text-muted-foreground">{src.description}</span>
                  </span>
                  {isActive && (
                    <span className="mt-1 text-label-xxs uppercase text-primary">Active</span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
        <div className="border-t px-2 py-1.5 text-label-xxs text-muted-foreground">
          Icon legend: <Brain className="inline h-3 w-3" aria-hidden="true" /> local KB ·{" "}
          <Globe className="inline h-3 w-3" aria-hidden="true" /> web ·{" "}
          <Bot className="inline h-3 w-3" aria-hidden="true" /> LLM general knowledge
        </div>
      </PopoverContent>
    </Popover>
  )
}
