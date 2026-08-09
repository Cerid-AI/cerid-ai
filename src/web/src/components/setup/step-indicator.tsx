// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
    <div className="flex items-center justify-center gap-0.5">
      {steps.map((step, i) => {
        const state = getState(i)
        return (
          <div key={i} className="flex items-center gap-0.5">
            {i > 0 && (
              <div
                className={cn(
                  "h-px w-1.5 shrink-0",
                  state === "pending" ? "bg-muted-foreground/20" : "bg-brand/40",
                )}
              />
            )}
            <div
              className={cn(
                "flex items-center gap-0.5 rounded-full px-1 py-0.5 text-label-xxs font-medium whitespace-nowrap transition-colors",
                state === "active" && "bg-brand/10 text-brand",
                state === "completed" && "text-green-600 dark:text-green-400",
                // Skipped pips: muted color + line-through so the user can
                // clearly distinguish "I deliberately skipped this" from
                // "I completed this" at a glance (F-04 step-indicator).
                state === "skipped" && "text-muted-foreground/60 line-through decoration-from-font",
                state === "pending" && "text-muted-foreground/40",
              )}
              aria-label={`Step ${i + 1}: ${step.label} — ${state}`}
            >
              {state === "completed" && <Check className="h-2 w-2" />}
              {state === "skipped" && <SkipForward className="h-2 w-2 no-underline" />}
              {state === "active" && (
                <div className="h-1.5 w-1.5 rounded-full bg-brand" />
              )}
              {state === "pending" && (
                <div className="h-1 w-1 rounded-full bg-muted-foreground/30" />
              )}
              <span className="hidden sm:inline">{step.shortLabel}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
