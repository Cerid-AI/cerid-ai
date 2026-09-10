// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useQuery } from "@tanstack/react-query"
import { Cpu, Cloud, HardDrive } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { fetchHealthStatus, fetchSystemCheck } from "@/lib/api"
import { backendSummary, deriveRecommendation } from "@/lib/hardware-profile"
import { cn } from "@/lib/utils"
import type { RecommendedLocalBackend } from "@/lib/types"

const ICON: Record<RecommendedLocalBackend, typeof Cpu> = {
  quenchforge: Cpu,
  ollama: HardDrive,
  cloud: Cloud,
}

/** ``internal_llm_provider`` as the pill's three-way backend identity. */
const PROVIDER_BACKEND: Record<string, RecommendedLocalBackend> = {
  quenchforge: "quenchforge",
  ollama: "ollama",
  openrouter: "cloud",
}

/**
 * Compact at-a-glance indicator of the active inference backend.
 *
 * Mounted in the status bar. The label is the provider ``/health/status``
 * reports, which is the canonical runtime value. The hardware recommendation
 * from ``/system-check`` is only a fallback: the pill previously used it as a
 * stand-in for "active", so a host running quenchforge on hardware that would
 * be recommended cloud was labelled "Cloud".
 */
export function BackendStatusPill() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["system-check"],
    queryFn: fetchSystemCheck,
    staleTime: 60_000,
    refetchInterval: 300_000,
    retry: 1,
  })
  // eslint-disable-next-line cerid/no-query-error-as-empty -- deliberate: an unreachable /health/status leaves the provider undefined and the pill falls back to the hardware recommendation below, which is what it showed before this query existed
  const { data: health } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    staleTime: 60_000,
    refetchInterval: 300_000,
    retry: 1,
  })

  if (isLoading || isError || !data) {
    return null
  }

  const configured = health?.internal_llm_provider
    ? PROVIDER_BACKEND[health.internal_llm_provider]
    : undefined
  const active: RecommendedLocalBackend =
    configured ?? data.recommended_local_backend ?? deriveRecommendation(data)
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
