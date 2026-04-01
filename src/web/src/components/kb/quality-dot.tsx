// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"

interface QualityDotProps {
  score: number | undefined | null
  className?: string
}

function getTier(score: number) {
  if (score >= 0.8) return { color: "bg-teal-500", label: "Excellent", sparkle: true }
  if (score >= 0.6) return { color: "bg-green-500", label: "Good", sparkle: false }
  if (score >= 0.3) return { color: "bg-amber-500", label: "Fair", sparkle: false }
  return { color: "bg-red-500", label: "Poor", sparkle: false }
}

export function QualityDot({ score, className }: QualityDotProps) {
  if (score == null) return null

  const tier = getTier(score)
  const pct = Math.round(score * 100)

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("relative inline-flex items-center", className)}>
            <span className={cn("h-2 w-2 rounded-full", tier.color)} />
            {tier.sparkle && (
              <Sparkles className="absolute -right-1 -top-1 h-2.5 w-2.5 text-teal-400" />
            )}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">
          Quality: {pct}% ({tier.label})
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
