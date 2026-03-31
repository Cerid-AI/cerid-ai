// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { Collapsible } from "radix-ui"
import { ChevronRight, FileText } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { DomainBadge } from "@/components/ui/domain-badge"
import { cn } from "@/lib/utils"
import type { SourceRef } from "@/lib/types"

interface SourceAttributionProps {
  sources: SourceRef[]
}

/**
 * Deduplicate sources at the file level — show each artifact once with
 * the highest relevance and quality score across its chunks.
 */
function deduplicateByArtifact(sources: SourceRef[]): SourceRef[] {
  const map = new Map<string, SourceRef>()
  for (const src of sources) {
    const existing = map.get(src.artifact_id)
    if (!existing) {
      map.set(src.artifact_id, src)
    } else {
      // Keep highest relevance, but always take the best quality_score from either
      const bestQuality = Math.max(src.quality_score ?? 0, existing.quality_score ?? 0) || undefined
      const winner = src.relevance > existing.relevance ? src : existing
      map.set(src.artifact_id, { ...winner, quality_score: bestQuality })
    }
  }
  return [...map.values()]
}

export function SourceAttribution({ sources }: SourceAttributionProps) {
  const [open, setOpen] = useState(false)

  const dedupedSources = deduplicateByArtifact(sources)

  if (dedupedSources.length === 0) return null

  return (
    <Collapsible.Root open={open} onOpenChange={setOpen} className="mt-1">
      <Collapsible.Trigger asChild>
        <button
          className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          aria-label={`${open ? "Hide" : "Show"} ${dedupedSources.length} source${dedupedSources.length !== 1 ? "s" : ""}`}
        >
          <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
          <FileText className="h-3 w-3" />
          <span>{dedupedSources.length} source{dedupedSources.length !== 1 ? "s" : ""}</span>
        </button>
      </Collapsible.Trigger>
      <Collapsible.Content className="mt-1.5 space-y-1">
        {dedupedSources.map((src) => (
          <SourceCard key={src.artifact_id} source={src} />
        ))}
      </Collapsible.Content>
    </Collapsible.Root>
  )
}

function SourceCard({ source }: { source: SourceRef }) {
  const relevancePct = Math.round(source.relevance * 100)

  return (
    <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-2.5 py-1.5 text-xs">
      <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
      <span className="min-w-0 truncate font-medium">{source.filename}</span>
      <DomainBadge domain={source.domain} />
      {source.sub_category && source.sub_category !== "general" && (
        <Badge variant="secondary" className="text-[10px]">
          {source.sub_category}
        </Badge>
      )}
      <div className="ml-auto flex items-center gap-1.5 shrink-0">
        {source.quality_score != null && (
          <span className={cn(
            "tabular-nums text-[10px]",
            source.quality_score >= 0.8 ? "text-green-600 dark:text-green-400" :
            source.quality_score >= 0.6 ? "text-blue-600 dark:text-blue-400" :
            source.quality_score >= 0.4 ? "text-yellow-600 dark:text-yellow-400" :
                                          "text-red-600 dark:text-red-400",
          )}>
            Q{Math.round(source.quality_score * 100)}
          </span>
        )}
        {relevancePct > 0 && (
          <span className="tabular-nums text-muted-foreground">
            {relevancePct}%
          </span>
        )}
      </div>
    </div>
  )
}