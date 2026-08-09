// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useQuery } from "@tanstack/react-query"
import { Cpu, Cloud, HardDrive } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { fetchSystemCheck } from "@/lib/api"
import { backendSummary, deriveRecommendation } from "@/lib/hardware-profile"
import { cn } from "@/lib/utils"
import type { RecommendedLocalBackend } from "@/lib/types"

const ICON: Record<RecommendedLocalBackend, typeof Cpu> = {
  quenchforge: Cpu,
  ollama: HardDrive,
  cloud: Cloud,
}

/**
 * Compact at-a-glance indicator of the active inference backend.
 *
 * Mounted in the status bar. Reads `/system-check` to drive the label —
 * for now it uses the *recommended* backend as a stand-in for "active",
 * since the actual ``INTERNAL_LLM_PROVIDER`` value isn't surfaced over
 * the API yet. Wire-up to the canonical provider value follows in PR 3b.
 */
export function BackendStatusPill() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["system-check"],
    queryFn: fetchSystemCheck,
    staleTime: 60_000,
    refetchInterval: 300_000,
    retry: 1,
  })

  if (isLoading || isError || !data) {
    return null
  }

  const active: RecommendedLocalBackend =
    data.recommended_local_backend ?? deriveRecommendation(data)
  const summary = backendSummary(active)
  const Icon = ICON[active]

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className={cn(
            "flex cursor-default items-center gap-1 rounded-full border px-2 py-0.5 text-label-xs",
            summary.tone === "local"
              ? "border-brand/30 bg-brand/5 text-brand"
              : "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
          )}
        >
          <Icon className="h-3 w-3" aria-hidden="true" />
          <span>{summary.label}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs space-y-1">
        <p className="font-medium">Inference backend: {summary.label}</p>
        <p className="text-muted-foreground">
          {summary.tone === "local"
            ? "Pipeline LLM calls route to a local server."
            : "Pipeline LLM calls route through the configured cloud provider."}
        </p>
        <p className="text-muted-foreground/80">
          Change in Settings &rarr; Inference Backend.
        </p>
      </TooltipContent>
    </Tooltip>
  )
}
