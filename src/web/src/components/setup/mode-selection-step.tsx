// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"

interface ModeSelectionStepProps {
  selectedMode: "simple" | "advanced"
  onSelectMode: (mode: "simple" | "advanced") => void
  configSummary: {
    providerCount: number
    domainCount: number
    ollamaEnabled: boolean
  }
}

export function ModeSelectionStep({
  selectedMode,
  onSelectMode,
  configSummary,
}: ModeSelectionStepProps) {
  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Sparkles className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Choose Your Mode</h3>

      <p className="mb-1 text-center text-xs text-muted-foreground">
        You configured {configSummary.providerCount} LLM provider{configSummary.providerCount !== 1 ? "s" : ""},
        {" "}{configSummary.domainCount} KB domain{configSummary.domainCount !== 1 ? "s" : ""},
        and Ollama is {configSummary.ollamaEnabled ? "enabled" : "disabled"}.
      </p>
      <p className="mb-4 text-center text-xs text-muted-foreground">
        You can change this anytime from the sidebar.
      </p>

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
          <p className="text-sm font-medium">Simple</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Clean chat interface — KB toggles, verification, and analytics hidden.
            Perfect for everyday use.
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
    </>
  )
}
