// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useQuery } from "@tanstack/react-query"
import { Terminal, Zap } from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { fetchHealthStatus, fetchProviderCredits } from "@/lib/api"
import { isLocalProvider } from "@/lib/types"
import { cn } from "@/lib/utils"
import {
  InferenceLaneRows,
  degradedLaneSummary,
  degradedLanes,
  isOnBoxServing,
  laneModelLabel,
  laneModelUnset,
  providerLabel,
  readInferenceLanes,
} from "@/components/monitoring/inference-lanes"
import { TrustScoreChip } from "@/components/trust-score"
import { BackendStatusPill } from "@/components/layout/backend-status-pill"
import { LicenseStatusBadge } from "@/components/settings/license-notice"

const SERVICE_INFO: Record<string, { purpose: string; tech: string }> = {
  chromadb: { purpose: "Vector embeddings & semantic search", tech: "ChromaDB" },
  redis: { purpose: "Cache, session state, pub/sub", tech: "Redis" },
  neo4j: { purpose: "Knowledge graph & relationships", tech: "Neo4j" },
}

interface StatusBarProps {
  consoleOpen?: boolean
  onToggleConsole?: () => void
  consoleUnreadCount?: number
  /** Feature tier — controls whether the gold divider is shown. Only Pro+
   * surfaces gold trim; community/default tier uses a neutral border. */
  featureTier?: string
}

export function StatusBar({
  consoleOpen,
  onToggleConsole,
  consoleUnreadCount = 0,
  featureTier = "community",
}: StatusBarProps) {
  // Audit P1.9: gold top border was always-on, competing with the teal
  // accent for users on the default tier. Gate to Pro+ tiers only.
  const tierGold = featureTier === "pro" || featureTier === "enterprise"
  const { data: health, isError, isLoading, dataUpdatedAt } = useQuery({
    // Keyed by endpoint, not by topic: react-query caches on the key alone,
    // so sharing ["health"] with a component that fetches /health handed one
    // of them the other's payload.
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
    retry: 1,
  })

  const { data: credits } = useQuery({
    queryKey: ["provider-credits"],
    queryFn: fetchProviderCredits,
    // CH-CREDITS: keep this responsive so a recovered backend status
    // ("ok") clears a previously-cached "exhausted" footer promptly.
    // Short stale window + frequent refetch + refetch on focus/reconnect;
    // a successful fetch replaces the cached value (no placeholderData),
    // so the indicator never sticks on a stale "exhausted" state.
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: 1,
    staleTime: 5_000,
  })

  // The routing snapshot is the only surface that knows a lane is answering
  // from its fallback. /health folds that into its own `status`; /health/status
  // (what this bar polls) does not, so the fold happens here — otherwise the
  // dot stays green while every rerank is served by the CPU ONNX path.
  const lanes = readInferenceLanes(health?.inference_routing)
  const laneProblems = degradedLanes(lanes)
  const transportStatus = isLoading ? "loading" : isError ? "error" : health?.status ?? "unknown"
  const status =
    transportStatus === "healthy" && laneProblems.length > 0 ? "degraded" : transportStatus
  const lastChecked = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : "—"

  const services = health?.services
  const connectedCount = services
    ? Object.values(services).filter((s) => s === "connected").length
    : 0
  const totalCount = services ? Object.keys(services).length : 0

  return (
    <TooltipProvider delayDuration={200}>
      <div
        className={cn(
          // Task 3.7 — hidden <md so it doesn't collide with the fixed
          // bottom tab bar, which occupies the same screen position.
          "hidden h-8 items-center gap-4 border-t bg-muted/40 px-4 text-xs text-muted-foreground md:flex",
          tierGold ? "border-[rgba(212,175,55,0.22)]" : "border-border",
        )}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex cursor-default items-center gap-1.5">
              <div
                className={cn(
                  "h-2 w-2 rounded-full",
                  status === "healthy" && "bg-green-500 glow-teal",
                  status === "degraded" && "bg-yellow-500",
                  status === "loading" && "bg-muted-foreground/50",
                  (status === "error" || status === "unknown") && "bg-red-500"
                )}
                aria-hidden="true"
              />
              {/* Scope, not a whole-system grade: this dot sees datastore
                  transports and inference lanes. Latency and verification
                  coverage are graded in Diagnostics, which is why the two
                  used to contradict each other. */}
              <span data-testid="status-bar-verdict">
                {status === "healthy" && "All services connected"}
                {status === "degraded" &&
                  (transportStatus === "healthy"
                    ? `Inference degraded: ${degradedLaneSummary(lanes)}`
                    : "Some services degraded")}
                {status === "error" && "Connection error"}
                {status === "loading" && "Checking..."}
                {status === "unknown" && "Unknown status"}
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="top" className="space-y-1">
            <p className="font-medium">Datastores and inference lanes: {status}</p>
            {services && (
              <p className="text-muted-foreground">
                {connectedCount}/{totalCount} services connected
              </p>
            )}
            {laneProblems.length > 0 && (
              <p className="text-amber-700 dark:text-amber-400">
                {laneProblems.length} inference lane
                {laneProblems.length === 1 ? "" : "s"} serving from a fallback
              </p>
            )}
            <p className="text-muted-foreground">
              Latency and verification coverage are graded in Diagnostics.
            </p>
            <p className="text-muted-foreground">Last checked: {lastChecked}</p>
          </TooltipContent>
        </Tooltip>

        {/* Unlicensed-Pro marker: always present while paid features run
            without a license, so the state is visible from any screen. */}
        <LicenseStatusBadge />

        {health?.degradation_tier && health.degradation_tier !== "full" && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={cn(
                "rounded px-1.5 py-0.5 text-label-xs font-semibold uppercase tracking-wider",
                health.degradation_tier === "lite" && "bg-yellow-500/20 text-yellow-400",
                health.degradation_tier === "direct" && "bg-orange-500/20 text-orange-400",
                health.degradation_tier === "cached" && "bg-red-500/20 text-red-400",
                health.degradation_tier === "offline" && "bg-red-500/30 text-red-300 animate-pulse",
              )}>
                {health.degradation_tier}
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              <p className="font-medium">Degraded: {health.degradation_tier} tier</p>
              <div className="mt-1 space-y-0.5 text-xs">
                <p>Retrieve: {health.can_retrieve ? "\u2713" : "\u2717"}</p>
                <p>Verify: {health.can_verify ? "\u2713" : "\u2717"}</p>
                <p>Generate: {health.can_generate ? "\u2713" : "\u2717"}</p>
              </div>
              {health.pipeline_providers && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {Object.values(health.pipeline_providers).filter(isOnBoxServing).length}/
                  {Object.values(health.pipeline_providers).length} stages local
                </p>
              )}
            </TooltipContent>
          </Tooltip>
        )}

        {services && (
          <div className="flex items-center gap-3">
            {Object.entries(services).map(([name, state]) => {
              const info = SERVICE_INFO[name]
              const connected = state === "connected"
              return (
                <Tooltip key={name}>
                  <TooltipTrigger asChild>
                    <span className={cn("cursor-default", !connected && "text-destructive")}>
                      {name}: {state}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="space-y-1">
                    <p className="font-medium">{info?.tech ?? name}</p>
                    {info && <p className="text-muted-foreground">{info.purpose}</p>}
                    <p className={cn(connected ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400")}>
                      Status: {connected ? "Connected \u2713" : "Disconnected \u2717"}
                    </p>
                    <p className="text-muted-foreground">Last checked: {lastChecked}</p>
                  </TooltipContent>
                </Tooltip>
              )
            })}
          </div>
        )}

        {/* OpenRouter status indicator */}
        {health?.openrouter_auth_ok === false && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex items-center gap-1 rounded bg-red-500/20 px-1.5 py-0.5 text-label-xs font-semibold text-red-400 animate-pulse">
                <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
                OpenRouter: Auth Error
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="space-y-1">
              <p className="font-medium text-red-400">OpenRouter Authentication Failed</p>
              <p className="text-muted-foreground">API key may be invalid or expired. Verification and external LLM calls will fail until it is restored — there is no fallback gateway. Local Ollama-served stages are unaffected.</p>
              <p className="text-muted-foreground">Check your OPENROUTER_API_KEY in .env</p>
            </TooltipContent>
          </Tooltip>
        )}

        {health?.circuit_breakers?.openrouter === "open" && health?.openrouter_auth_ok !== false && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex items-center gap-1 rounded bg-orange-500/20 px-1.5 py-0.5 text-label-xs font-semibold text-orange-400">
                <span className="h-1.5 w-1.5 rounded-full bg-orange-500" />
                OpenRouter: Circuit Open
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="space-y-1">
              <p className="font-medium text-orange-400">OpenRouter Circuit Breaker Open</p>
              <p className="text-muted-foreground">Too many consecutive failures. OpenRouter calls are paused until the circuit resets; stages routed to local Ollama continue unaffected.</p>
            </TooltipContent>
          </Tooltip>
        )}

        {/* Local pipeline indicator (ollama | quenchforge — E1 R5 / CR-024).
            Provider and model come from the LLM lane of the routing snapshot:
            internal_llm_provider / internal_llm_model are /settings fields that
            /health/status has never emitted, so reading them here printed
            "Ollama: active" on every deployment regardless of the backend. */}
        {health?.pipeline_providers && (() => {
          const stageProviders = Object.values(health.pipeline_providers)
          const localCount = stageProviders.filter(isOnBoxServing).length
          const totalStages = stageProviders.length
          const llmLane = lanes.find((l) => l.lane === "llm")
          // Before any lane has answered, name the configured backend, then the
          // one the pipeline table names. Defaulting to "Ollama" here is how a
          // quenchforge host came to report "Ollama: active".
          const configuredLocal =
            (isLocalProvider(health.internal_llm_provider) ? health.internal_llm_provider : undefined)
            ?? stageProviders.find(isLocalProvider)
          const localLabel = llmLane
            ? providerLabel(llmLane.provider)
            : configuredLocal
              ? providerLabel(configuredLocal)
              : "Local inference"
          const modelLabel = llmLane
            ? laneModelUnset(llmLane)
              ? "no model pinned"
              : laneModelLabel(llmLane)
            : health.internal_llm_model ?? ""
          const laneWarning = laneProblems.length > 0 || (llmLane != null && laneModelUnset(llmLane))
          if (localCount > 0) {
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    data-testid="local-pipeline-chip"
                    className={cn(
                      "flex items-center gap-1 text-label-xs",
                      laneWarning
                        ? "text-amber-700 dark:text-amber-400"
                        : "text-green-600 dark:text-green-400",
                    )}
                  >
                    <span
                      className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        laneWarning ? "bg-amber-500" : "bg-green-500",
                      )}
                    />
                    {localLabel}
                    {modelLabel ? `: ${modelLabel}` : ""} ({localCount}/{totalStages} local
                    {laneProblems.length > 0 ? ` · ${laneProblems.length} degraded` : ""})
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" className="space-y-1">
                  <p className="font-medium">{localLabel} — on-box inference</p>
                  <p className="text-muted-foreground">
                    {localCount} of {totalStages} pipeline stages served on this machine ($0)
                  </p>
                  <InferenceLaneRows lanes={lanes} />
                </TooltipContent>
              </Tooltip>
            )
          }
          return (
            <Tooltip>
              <TooltipTrigger asChild>
                <span data-testid="local-pipeline-chip" className="inline-flex items-center gap-1 text-label-xs text-yellow-500/70" title="No local model pipeline stages. Install Ollama for local inference.">
                  <Zap className="size-3" aria-hidden="true" />
                  0 local
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="space-y-1">
                <p>All pipeline stages use cloud APIs. Enable Ollama for faster local processing.</p>
                <InferenceLaneRows lanes={lanes} />
              </TooltipContent>
            </Tooltip>
          )
        })()}

        {/* Backend status pill — shows the active inference backend
            (ollama / quenchforge / cloud) at a glance. Reads /system-check;
            no wire-up to the canonical INTERNAL_LLM_PROVIDER value yet. */}
        <BackendStatusPill />

        {/* TrustScore chip — pure presentation, no effect on retrieval/generation */}
        <TrustScoreChip />

        {/* Agent Console toggle */}
        {onToggleConsole && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={onToggleConsole}
                className={cn(
                  "relative flex items-center gap-1 rounded px-1.5 py-0.5 text-label-xs transition-colors",
                  consoleOpen
                    ? "bg-teal-500/20 text-teal-400"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Terminal className="h-3 w-3" />
                <span className="hidden sm:inline">Console</span>
                {!consoleOpen && consoleUnreadCount > 0 && (
                  <span className="absolute -right-1 -top-1 flex h-3.5 min-w-[14px] animate-pulse items-center justify-center rounded-full bg-teal-500 px-1 text-label-xxs font-bold text-white"> {/* drift-allowed: notification-badge minimum width keeps single/double-digit counts centered */}
                    {consoleUnreadCount > 99 ? "99+" : consoleUnreadCount}
                  </span>
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top">
              {consoleOpen ? "Close agent console" : "Open agent console"}
            </TooltipContent>
          </Tooltip>
        )}

        {/* Credits indicator — pushed to the right */}
        {credits?.configured && (
          <div className="ml-auto">
            <Tooltip>
              <TooltipTrigger asChild>
                <a
                  href={credits.top_up_url ?? "https://openrouter.ai/settings/credits"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cn(
                    "cursor-pointer font-medium tabular-nums transition-colors hover:underline",
                    credits.status === "ok" && "text-green-600 dark:text-green-400",
                    credits.status === "low" && "text-yellow-600 dark:text-yellow-400",
                    credits.status === "exhausted" && "text-red-600 dark:text-red-400",
                    (credits.status === "error" || credits.balance == null) && "text-muted-foreground",
                  )}
                >
                  {credits.status === "exhausted" ? (
                    "Credits exhausted"
                  ) : credits.balance != null ? (
                    `$${credits.balance.toFixed(2)}`
                  ) : (
                    "Credits: —"
                  )}
                </a>
              </TooltipTrigger>
              <TooltipContent side="top" className="space-y-1">
                <p className="font-medium">OpenRouter Credits</p>
                <p className="text-muted-foreground">Balance: ${credits.balance?.toFixed(2)}</p>
                {credits.usage_daily != null && (
                  <p className="text-muted-foreground">Today: ${credits.usage_daily.toFixed(4)}</p>
                )}
                {credits.usage_weekly != null && (
                  <p className="text-muted-foreground">This week: ${credits.usage_weekly.toFixed(2)}</p>
                )}
                {credits.usage_monthly != null && (
                  <p className="text-muted-foreground">This month: ${credits.usage_monthly.toFixed(2)}</p>
                )}
                {credits.warning && (
                  <p className="font-medium text-amber-600 dark:text-yellow-400">{credits.warning}</p>
                )}
                <p className="text-muted-foreground">Click to add credits</p>
              </TooltipContent>
            </Tooltip>
          </div>
        )}
      </div>
    </TooltipProvider>
  )
}
