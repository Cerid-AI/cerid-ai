// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Check, SkipForward } from "lucide-react"
import { cn } from "@/lib/utils"

export type StepState = "completed" | "active" | "pending" | "skipped"

export interface StepDef {
  label: string
  shortLabel: string
}

interface StepIndicatorProps {
  steps: StepDef[]
  currentStep: number
  skippedSteps: Set<number>
}

export function StepIndicator({ steps, currentStep, skippedSteps }: StepIndicatorProps) {
  function getState(index: number): StepState {
    if (skippedSteps.has(index)) return "skipped"
    if (index < currentStep) return "completed"
    if (index === currentStep) return "active"
    return "pending"
  }

  return (
    <div className="flex items-center gap-1">
      {steps.map((step, i) => {
        const state = getState(i)
        return (
          <div key={i} className="flex items-center gap-1">
            {i > 0 && (
              <div
                className={cn(
                  "h-px w-2",
                  state === "pending" ? "bg-muted-foreground/20" : "bg-brand/40",
                )}
              />
            )}
            <div
              className={cn(
                "flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium transition-colors",
                state === "active" && "bg-brand/10 text-brand",
                state === "completed" && "text-green-600 dark:text-green-400",
                state === "skipped" && "text-muted-foreground/50",
                state === "pending" && "text-muted-foreground/40",
              )}
            >
              {state === "completed" && <Check className="h-2.5 w-2.5" />}
              {state === "skipped" && <SkipForward className="h-2.5 w-2.5" />}
              {state === "active" && (
                <div className="h-1.5 w-1.5 rounded-full bg-brand" />
              )}
              {state === "pending" && (
                <div className="h-1.5 w-1.5 rounded-full bg-muted-foreground/30" />
              )}
              <span className="hidden sm:inline">{step.shortLabel}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
