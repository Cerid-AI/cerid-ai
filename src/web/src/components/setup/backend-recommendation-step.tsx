// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useMemo } from "react"
import { Badge } from "@/components/ui/badge"
import { Cpu, Cloud, HardDrive, Sparkles, Check } from "lucide-react"
import { cn } from "@/lib/utils"
import { backendOptionsForHardware } from "@/lib/hardware-profile"
import { ModelCompatStatus } from "@/components/settings/model-compat-status"
import type { RecommendedLocalBackend, SystemCheckResponse } from "@/lib/types"

interface BackendRecommendationStepProps {
  /** Result from /system-check; used to derive the recommendation. */
  systemCheck: SystemCheckResponse | null
  /** Current user selection (null = user hasn't picked yet). */
  selected: RecommendedLocalBackend | null
  /** Called when the user picks a backend. */
  onSelect: (id: RecommendedLocalBackend) => void
}

const ICON_FOR_BACKEND = {
  ollama: HardDrive,
  quenchforge: Cpu,
  cloud: Cloud,
} as const

/**
 * Step 1 — Backend Recommendation. Shown after Welcome/System Check.
 *
 * Drives `INTERNAL_LLM_PROVIDER`. The recommendation is hardware-aware:
 * Intel Mac + AMD discrete → quenchforge; Apple Silicon / CUDA / ROCm → ollama;
 * unsupported local hardware → cloud. The user can override the recommendation.
 */
export function BackendRecommendationStep({
  systemCheck,
  selected,
  onSelect,
}: BackendRecommendationStepProps) {
  const { options, defaultId } = useMemo(
    () => backendOptionsForHardware(systemCheck),
    [systemCheck],
  )

  const activeId = selected ?? defaultId

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Sparkles className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Inference Backend</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">
        Where should Cerid send model requests?
      </p>

      <div className="space-y-2">
        {options.map((opt) => {
          const Icon = ICON_FOR_BACKEND[opt.id]
          const isActive = activeId === opt.id
          return (
            <button
              key={opt.id}
              type="button"
              onClick={() => onSelect(opt.id)}
              className={cn(
                "flex w-full items-start gap-3 rounded-lg border bg-card px-3 py-3 text-left transition-colors",
                "hover:border-brand/40",
                isActive && "border-brand/60 ring-1 ring-brand/30 bg-brand/5",
              )}
              aria-pressed={isActive}
            >
              <div
                className={cn(
                  "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                  isActive ? "bg-brand/15 text-brand" : "bg-muted text-muted-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{opt.label}</span>
                  <span className="flex items-center gap-1.5">
                    {opt.badge && (
                      <Badge
                        variant="outline"
                        className={cn(
                          "border-brand/30 bg-brand/10 text-brand",
                          opt.badge === "Detected" &&
                            "border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400",
                        )}
                      >
                        {opt.badge}
                      </Badge>
                    )}
                    {/* Explicit label, not just a border tint — clicking the
                        already-highlighted default must read as "confirmed",
                        not as a dead control (GUI spec NICE 14 / defect #5). */}
                    {isActive && (
                      <span className="flex items-center gap-1 text-label-xs font-medium text-brand">
                        <Check className="h-3.5 w-3.5" aria-hidden="true" />
                        Selected
                      </span>
                    )}
                  </span>
                </div>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  {opt.blurb}
                </p>
              </div>
            </button>
          )
        })}
      </div>

      {systemCheck && (
        <div className="mt-4 rounded-lg border bg-muted/30 p-3 text-label-xs text-muted-foreground">
          <span className="font-medium text-foreground">Detected:</span>{" "}
          {systemCheck.os} · {systemCheck.cpu} · {systemCheck.gpu}
          {systemCheck.gpu_type && (
            <span> ({systemCheck.gpu_type})</span>
          )}
        </div>
      )}

      {/* Hardware-aware model compatibility + recommended local models */}
      <div className="mt-3">
        <ModelCompatStatus compact />
      </div>
    </>
  )
}
