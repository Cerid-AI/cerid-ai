// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Sparkles, Cpu, Zap } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

interface HardwareInfo {
  ram_gb: number
  cpu: string
  gpu: string
  gpu_acceleration: string
}

/** Recommend a mode based on hardware capabilities. */
function recommendMode(hw: HardwareInfo | null): {
  mode: "simple" | "advanced"
  reason: string
} {
  if (!hw || hw.ram_gb === 0) return { mode: "simple", reason: "" }

  // Advanced recommended for capable systems (16GB+ RAM or GPU)
  if (hw.ram_gb >= 16 || hw.gpu_acceleration !== "none") {
    return {
      mode: "advanced",
      reason: hw.gpu_acceleration !== "none"
        ? `${hw.ram_gb}GB RAM + ${hw.gpu_acceleration} GPU — your system can run all pipeline features`
        : `${hw.ram_gb}GB RAM — your system can handle verification, reranking, and smart routing`,
    }
  }

  return {
    mode: "simple",
    reason: `${hw.ram_gb}GB RAM — Simple mode uses fewer resources for smooth performance`,
  }
}

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
  hardware?: HardwareInfo | null
}

export function ModeSelectionStep({
  selectedMode,
  onSelectMode,
  configSummary,
  hardware,
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

  const recommendation = recommendMode(hardware ?? null)

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

      {/* Hardware recommendation */}
      {hardware && hardware.ram_gb > 0 && (
        <div className="mb-3 rounded-lg border border-brand/20 bg-brand/5 px-3 py-2">
          <div className="flex items-center gap-2">
            <Cpu className="h-3.5 w-3.5 text-brand shrink-0" />
            <p className="text-[11px] text-muted-foreground">
              <span className="font-medium text-foreground">Recommended: {recommendation.mode === "advanced" ? "Advanced" : "Simple"}</span>
              {recommendation.reason && ` — ${recommendation.reason}`}
            </p>
          </div>
          {hardware.gpu_acceleration !== "none" && (
            <div className="mt-1.5 flex items-center gap-1.5">
              <Zap className="h-3 w-3 text-green-500" />
              <p className="text-[10px] text-green-600 dark:text-green-400">
                GPU acceleration available ({hardware.gpu_acceleration}) — embeddings and reranking will be faster
              </p>
            </div>
          )}
        </div>
      )}

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
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">Clean & Simple</p>
            {recommendation.mode === "simple" && hardware && hardware.ram_gb > 0 && (
              <Badge variant="outline" className="text-[9px] px-1.5 py-0 border-brand/30 text-brand">
                Recommended
              </Badge>
            )}
          </div>
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
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">Advanced</p>
            {recommendation.mode === "advanced" && hardware && hardware.ram_gb > 0 && (
              <Badge variant="outline" className="text-[9px] px-1.5 py-0 border-brand/30 text-brand">
                Recommended
              </Badge>
            )}
          </div>
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
