// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Shield } from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import type { SourceRef } from "@/lib/types"

export function KBContextIndicator({ sources }: { sources?: SourceRef[] }) {
  if (!sources?.length) return null

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground/60 select-none cursor-default">
            <Shield className="h-3 w-3" />
            <span>KB context sent to LLM &middot; {sources.length} {sources.length === 1 ? "source" : "sources"}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          <p className="font-medium mb-1">Local documents included in this request:</p>
          <ul className="space-y-0.5">
            {sources.map((s, i) => (
              <li key={i} className="truncate">{s.filename} ({Math.round(s.relevance * 100)}%)</li>
            ))}
          </ul>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
