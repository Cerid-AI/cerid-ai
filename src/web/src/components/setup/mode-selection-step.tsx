// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"

interface ModeSelectionStepProps {
  selectedMode: "simple" | "advanced"
  onSelectMode: (mode: "simple" | "advanced") => void
  configSummary: {
    providerCount: number
    providerNames: string[]
    domainCount: number
    ollamaEnabled: boolean
    ollamaModel: string | null
    documentCount: number
  }
}

export function ModeSelectionStep({
  selectedMode,
  onSelectMode,
  configSummary,
}: ModeSelectionStepProps) {
  const providerText = configSummary.providerNames.length > 0
    ? configSummary.providerNames.join(" + ") + " configured"
    : "No providers configured"

  const kbText = configSummary.documentCount > 0
    ? `${configSummary.documentCount} document${configSummary.documentCount !== 1 ? "s" : ""} ingested`
    : "0 documents"

  const ollamaText = configSummary.ollamaEnabled && configSummary.ollamaModel
    ? `Local LLM: ${configSummary.ollamaModel}`
    : "Local LLM: not configured"

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Sparkles className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Choose Your Mode</h3>

      <div className="mb-4 rounded-lg border bg-card px-3 py-2 text-center text-[11px] text-muted-foreground">
        {providerText} · {kbText} · {ollamaText}
      </div>

      <div className="space-y-2">
        <button
          type="button"
          onClick={() => onSelectMode("simple")}
          className={cn(
            "w-full rounded-lg border p-3 text-left transition-colors",
            selectedMode === "simple"
              ? "border-brand bg-brand/5"
              : "border-muted hover:border-muted-foreground/30",
          )}
        >
          <p className="text-sm font-medium">Clean & Simple</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            A clean chat focused on your knowledge — no technical controls visible.
            Perfect for everyday use. You can switch to Advanced anytime in Settings.
          </p>
        </button>
        <button
          type="button"
          onClick={() => onSelectMode("advanced")}
          className={cn(
            "w-full rounded-lg border p-3 text-left transition-colors",
            selectedMode === "advanced"
              ? "border-brand bg-brand/5"
              : "border-muted hover:border-muted-foreground/30",
          )}
        >
          <p className="text-sm font-medium">Advanced</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Full control — KB panel, verification, smart routing, feedback loop,
            and all pipeline settings visible.
          </p>
        </button>
      </div>

      <p className="mt-3 text-center text-[10px] text-muted-foreground">
        You can change this anytime from the sidebar.
      </p>
    </>
  )
}
