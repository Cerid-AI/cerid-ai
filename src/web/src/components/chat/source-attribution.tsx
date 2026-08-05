// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState } from "react"
import { Collapsible } from "radix-ui"
import { ChevronRight, FileText, Shield, Brain, Globe } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { DomainBadge } from "@/components/ui/domain-badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { SourceRef } from "@/lib/types"

interface SourceAttributionProps {
  sources: SourceRef[]
  /**
   * Rendering variant:
   * - "card" (default): collapsible list — chevron + filenames + relevance.
   * - "badge": compact tooltip badge with KB/memory/external counts. Replaces
   *   the previous standalone <KBContextIndicator>.
   *
   * Both consume the same `sources` array; pick the shape that fits the slot.
   */
  variant?: "card" | "badge"
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

export function SourceAttribution({ sources, variant = "card" }: SourceAttributionProps) {
  const [open, setOpen] = useState(false)

  if (variant === "badge") {
    return <SourceBadge sources={sources} />
  }

  // No client-side relevance floor: these are the sources the backend actually
  // included in the answer's context (already floored pre-rerank + top-k). The
  // post-rerank `relevance` is an ordinal cross-encoder sigmoid, so an absolute
  // 0.45 cutoff here hid real citations from grounded answers (CR-010).
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

/** Tooltip badge — KB/memory/external counts at a glance, with a full list
 *  on hover. Formerly the standalone <KBContextIndicator>; merged here so
 *  both shapes derive from the same prop contract. */
function SourceBadge({ sources }: { sources: SourceRef[] }) {
  if (!sources?.length) return null

  const kbCount = sources.filter((s) => s.source_type === "kb" || !s.source_type).length
  const memoryCount = sources.filter((s) => s.source_type === "memory").length
  const externalCount = sources.filter((s) => s.source_type === "external").length
  const hasMultipleTypes = (memoryCount > 0 || externalCount > 0)

  const label = hasMultipleTypes
    ? `${kbCount} KB · ${memoryCount} memory · ${externalCount} external`
    : `${sources.length} ${sources.length === 1 ? "source" : "sources"}`

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 text-label-sm text-muted-foreground select-none cursor-default">
            <Shield className="h-3 w-3" />
            <span>Context sent to LLM &middot; {label}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          <p className="font-medium mb-1">Sources included in this request:</p>
          <ul className="space-y-0.5">
            {sources.map((s, i) => {
              const SourceIcon = s.source_type === "memory" ? Brain : s.source_type === "external" ? Globe : FileText
              return (
                <li key={i} className="flex items-center gap-1 truncate">
                  <SourceIcon className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="truncate">
                    {s.filename} ({Math.round(s.relevance * 100)}%)
                  </span>
                </li>
              )
            })}
          </ul>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

function SourceCard({ source }: { source: SourceRef }) {
  const relevancePct = Math.round(source.relevance * 100)

  return (
    <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-2.5 py-1.5 text-xs">
      <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
      {source.source_url ? (
        <a
          href={source.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="min-w-0 truncate font-medium underline-offset-2 hover:underline"
        >
          {source.filename}
        </a>
      ) : (
        <span className="min-w-0 truncate font-medium">{source.filename}</span>
      )}
      <DomainBadge domain={source.domain} />
      {source.sub_category && source.sub_category !== "general" && (
        <Badge variant="secondary" className="text-label-xs">
          {source.sub_category}
        </Badge>
      )}
      <div className="ml-auto flex items-center gap-1.5 shrink-0">
        {source.quality_score != null && (
          <span className={cn(
            "tabular-nums text-label-xs",
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
