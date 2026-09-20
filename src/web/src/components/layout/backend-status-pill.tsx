// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useQuery } from "@tanstack/react-query"
import { Cpu, Cloud, HardDrive, TriangleAlert } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { fetchHealthStatus, fetchSystemCheck } from "@/lib/api"
import { backendSummary, deriveRecommendation } from "@/lib/hardware-profile"
import { cn } from "@/lib/utils"
import {
  isOnBoxServing,
  providerLabel,
  readInferenceLanes,
} from "@/components/monitoring/inference-lanes"
import type { RecommendedLocalBackend } from "@/lib/types"

const ICON: Record<RecommendedLocalBackend, typeof Cpu> = {
  quenchforge: Cpu,
  ollama: HardDrive,
  cloud: Cloud,
}

/** Icon for what is actually serving, including the in-process fallbacks. */
const SERVING_ICON: Record<string, typeof Cpu> = {
  quenchforge: Cpu,
  ollama: HardDrive,
  onnx: HardDrive,
  "in-process": HardDrive,
  sidecar: HardDrive,
  openrouter: Cloud,
}

/** ``internal_llm_provider`` as the pill's three-way backend identity. */
const PROVIDER_BACKEND: Record<string, RecommendedLocalBackend> = {
  quenchforge: "quenchforge",
  ollama: "ollama",
  openrouter: "cloud",
}

/**
 * Compact at-a-glance indicator of the inference backend actually in use.
 *
 * The label comes from the LLM lane of the ``/health/status`` routing snapshot:
 * which provider answered last, and whether that was the configured one. It
 * used to come from ``/system-check``'s hardware *recommendation*, which says
 * what the box could run — so a machine recommended Quenchforge but configured
 * for OpenRouter was labelled Quenchforge indefinitely, and a lane that had
 * fallen back looked identical to one serving normally.
 *
 * Until a lane has answered there is nothing observed to report, so the pill
 * names the provider ``/health/status`` says is configured, and only then the
 * hardware recommendation — labelled as one.
 */
export function BackendStatusPill() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["system-check"],
    queryFn: fetchSystemCheck,
    staleTime: 60_000,
    refetchInterval: 300_000,
    retry: 1,
  })
  // eslint-disable-next-line cerid/no-query-error-as-empty -- deliberate: an unreachable /health/status leaves the lane and the provider undefined and the pill falls back to the hardware recommendation below, which is what it showed before this query existed
  const { data: health } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
    retry: 1,
  })

  if (isLoading || isError || !data) {
    return null
  }

  const llmLane = readInferenceLanes(health?.inference_routing).find((l) => l.lane === "llm")
  const observed = llmLane != null && llmLane.serving !== "unknown"
  const configured = health?.internal_llm_provider
    ? PROVIDER_BACKEND[health.internal_llm_provider]
    : undefined
  const unobserved: RecommendedLocalBackend =
    configured ?? data.recommended_local_backend ?? deriveRecommendation(data)
  const summary = backendSummary(unobserved)

  const label = observed ? providerLabel(llmLane.serving) : summary.label
  const tone: "local" | "cloud" = observed
    ? isOnBoxServing(llmLane.serving)
      ? "local"
      : "cloud"
    : summary.tone
  const degraded = observed && llmLane.degraded
  const Icon = degraded
    ? TriangleAlert
    : observed
      ? SERVING_ICON[llmLane.serving.toLowerCase()] ?? (tone === "local" ? Cpu : Cloud)
      : ICON[unobserved]

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          data-testid="backend-status-pill"
          className={cn(
            "flex cursor-default items-center gap-1 rounded-full border px-2 py-0.5 text-label-xs",
            degraded
              ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
              : tone === "local"
                ? "border-brand/30 bg-brand/5 text-brand"
                : "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
          )}
        >
          <Icon className="h-3 w-3" aria-hidden="true" />
          <span>
            {label}
            {degraded ? " (fallback)" : observed || configured ? "" : " (recommended)"}
          </span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs space-y-1">
        <p className="font-medium">
          {observed
            ? `Serving inference: ${label}`
            : configured
              ? `Configured backend: ${label}`
              : `Recommended backend: ${label}`}
        </p>
        {observed ? (
          <p className="text-muted-foreground">
            {degraded
              ? `Configured for ${providerLabel(llmLane.provider)}; ${label} answered the last ${llmLane.fallbackCount} call${llmLane.fallbackCount === 1 ? "" : "s"}.`
              : tone === "local"
                ? "Pipeline LLM calls are being answered on this machine."
                : "Pipeline LLM calls are being answered by the configured cloud provider."}
          </p>
        ) : (
          <p className="text-muted-foreground">
            No LLM call has been observed yet, so this is the{" "}
            {configured ? "configured provider" : "hardware recommendation"}, not
            the serving backend.
          </p>
        )}
        {degraded && llmLane.degradedDetail && (
          <p className="text-amber-700 dark:text-amber-400">{llmLane.degradedDetail}</p>
        )}
        <p className="text-muted-foreground/80">
          Change in Settings &rarr; Inference Backend.
        </p>
      </TooltipContent>
    </Tooltip>
  )
}
