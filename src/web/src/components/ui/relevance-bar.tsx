// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { ProgressBar } from "@/components/ui/progress-bar"
import { cn, relativeRelevance } from "@/lib/utils"

interface RelevanceBarProps {
  relevance: number
  /** Scores of every result in the same list. Omit for a source that stands alone. */
  among?: readonly number[]
  className?: string
}

export function RelevanceBar({ relevance, among, className }: RelevanceBarProps) {
  const { pct, label } = relativeRelevance(relevance, among)
  return <ProgressBar pct={pct} size="sm" label={label} className={cn("w-10 shrink-0", className)} />
}
