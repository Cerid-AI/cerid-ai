// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Shield } from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import type { SourceRef } from "@/lib/types"

export function KBContextIndicator({ sources }: { sources?: SourceRef[] }) {
  if (!sources?.length) return null

  const kbCount = sources.filter((s) => s.source_type === "kb" || !s.source_type).length
  const memoryCount = sources.filter((s) => s.source_type === "memory").length
  const externalCount = sources.filter((s) => s.source_type === "external").length
  const hasMultipleTypes = (memoryCount > 0 || externalCount > 0)

  const label = hasMultipleTypes
    ? `${kbCount} KB \u00b7 ${memoryCount} memory \u00b7 ${externalCount} external`
    : `${sources.length} ${sources.length === 1 ? "source" : "sources"}`

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground select-none cursor-default">
            <Shield className="h-3 w-3" />
            <span>Context sent to LLM &middot; {label}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          <p className="font-medium mb-1">Sources included in this request:</p>
          <ul className="space-y-0.5">
            {sources.map((s, i) => (
              <li key={i} className="truncate">
                {s.source_type === "memory" ? "\uD83E\uDDE0" : s.source_type === "external" ? "\uD83C\uDF10" : "\uD83D\uDCC4"}{" "}
                {s.filename} ({Math.round(s.relevance * 100)}%)
              </li>
            ))}
          </ul>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
