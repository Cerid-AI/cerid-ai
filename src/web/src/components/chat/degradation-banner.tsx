// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, WifiOff, X, RefreshCw, Check, ChevronDown, ChevronRight, Copy } from "lucide-react"
import { Button } from "@/components/ui/button"
import { fetchHealthStatus } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { DegradationTier } from "@/lib/provider-capabilities"

const DISMISS_KEY = "cerid:degradation-banner-dismissed-tier"
const CHECK_COOLDOWN_MS = 8_000
const RECOVERY_TOAST_MS = 5_000

// Adaptive polling: back off when degraded, accelerate on recovery signal
const POLL_HEALTHY_MS = 15_000
const POLL_DEGRADED_INITIAL_MS = 10_000
const POLL_DEGRADED_MAX_MS = 60_000
const BACKOFF_FACTOR = 1.5

interface TierInfo {
  message: string
  action: string
  actionLabel: string
  severity: "warning" | "error"
  command: string
  hint: string
}

const TIER_INFO: Partial<Record<DegradationTier, TierInfo>> = {
  lite: {
    message: "Lite mode — reranking and graph features temporarily unavailable",
    action: "#health",
    actionLabel: "View Health",
    severity: "warning",
    command: "docker restart ai-companion-chroma",
    hint: "ChromaDB may need a restart. The system will auto-recover when the service is back.",
  },
  direct: {
    message: "Retrieval services down — responses use AI knowledge only, no KB context",
    action: "#health",
    actionLabel: "View Health",
    severity: "warning",
    command: "docker compose -f stacks/infrastructure/docker-compose.yml --env-file .env up -d",
    hint: "Multiple infrastructure services are down. Restart the infrastructure stack to restore KB retrieval.",
  },
  cached: {
    message: "AI providers unreachable — only cached responses available",
    action: "#settings",
    actionLabel: "Check Settings",
    severity: "error",
    command: "curl -I https://api.openrouter.ai",
    hint: "Check your network connection and API key. Verify OpenRouter is reachable and credits are available.",
  },
  offline: {
    message: "System offline — services are not responding",
    action: "#settings",
    actionLabel: "Check Settings",
    severity: "error",
    command: "./scripts/start-cerid.sh",
    hint: "Docker containers may have stopped. Run the start script to bring all services back online.",
  },
}

/**
 * Persistent banner shown when the system is operating in a degraded tier.
 *
 * Features:
 * - Adaptive polling: backs off when degraded, returns to normal on recovery
 * - "Check now" button with cooldown to prevent hammering
 * - Recovery toast: green "Connection restored" banner that auto-dismisses
 * - Dismissible per-session, re-shows if tier worsens
 */
export function DegradationBanner() {
  const queryClient = useQueryClient()
  const [checkCooldown, setCheckCooldown] = useState(false)
  const [showRecoveryToast, setShowRecoveryToast] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const recoveryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const prevTierRef = useRef<DegradationTier | null>(null)
  const degradedSinceRef = useRef<number | null>(null)

  // Compute adaptive poll interval
  const pollIntervalRef = useRef(POLL_HEALTHY_MS)

  const { data: health } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: () => pollIntervalRef.current,
    retry: 1,
    staleTime: 5_000,
  })

  const tier = (health?.degradation_tier ?? "full") as DegradationTier
  const info = tier !== "full" ? TIER_INFO[tier] : undefined
  const isDegraded = tier !== "full"

  // Adaptive polling: update interval based on current state
  useEffect(() => {
    if (!isDegraded) {
      // Healthy — return to normal cadence
      pollIntervalRef.current = POLL_HEALTHY_MS
      degradedSinceRef.current = null
    } else {
      // Degraded — back off over time
      if (!degradedSinceRef.current) {
        degradedSinceRef.current = Date.now()
        pollIntervalRef.current = POLL_DEGRADED_INITIAL_MS
      } else {
        const elapsed = Date.now() - degradedSinceRef.current
        const factor = Math.pow(BACKOFF_FACTOR, elapsed / 30_000) // backoff every 30s
        pollIntervalRef.current = Math.min(
          POLL_DEGRADED_INITIAL_MS * factor,
          POLL_DEGRADED_MAX_MS,
        )
      }
    }
  }, [isDegraded, health])

  // Detect recovery: was degraded, now healthy → show recovery toast
  useEffect(() => {
    const prevTier = prevTierRef.current
    if (prevTier && prevTier !== "full" && tier === "full") {
      setShowRecoveryToast(true)
      setDismissedTier(null)
      try { sessionStorage.removeItem(DISMISS_KEY) } catch { /* noop */ }
      recoveryTimerRef.current = setTimeout(() => {
        setShowRecoveryToast(false)
      }, RECOVERY_TOAST_MS)
    }
    prevTierRef.current = tier
    return () => {
      if (recoveryTimerRef.current) clearTimeout(recoveryTimerRef.current)
    }
  }, [tier])

  const [dismissedTier, setDismissedTier] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(DISMISS_KEY)
    } catch {
      return null
    }
  })

  const handleDismiss = useCallback(() => {
    if (tier && tier !== "full") {
      setDismissedTier(tier)
      try {
        sessionStorage.setItem(DISMISS_KEY, tier)
      } catch { /* noop */ }
    }
  }, [tier])

  const handleCheckNow = useCallback(() => {
    if (checkCooldown) return
    setCheckCooldown(true)
    // Immediately refetch health
    queryClient.invalidateQueries({ queryKey: ["health-status"] })
    // Reset poll interval to fast for quick recovery detection
    pollIntervalRef.current = POLL_DEGRADED_INITIAL_MS
    // Cooldown prevents hammering
    setTimeout(() => setCheckCooldown(false), CHECK_COOLDOWN_MS)
  }, [checkCooldown, queryClient])

  // Recovery toast (auto-dismissing green banner)
  if (showRecoveryToast) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 border-b border-green-500/20 bg-green-500/10 px-4 py-1.5 text-xs"
      >
        <Check className="h-3.5 w-3.5 shrink-0 text-green-600 dark:text-green-400" />
        <span className="flex-1 text-green-700 dark:text-green-400">
          Connection restored — all services operational
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
          onClick={() => setShowRecoveryToast(false)}
          aria-label="Dismiss recovery notification"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
    )
  }

  // Don't show degradation banner if healthy, no tier info, or dismissed
  if (!isDegraded || !info || dismissedTier === tier) return null

  const isError = info.severity === "error"
  const textColor = isError ? "text-destructive" : "text-yellow-600 dark:text-yellow-400"
  const textColorStrong = isError ? "text-destructive" : "text-yellow-700 dark:text-yellow-400"

  const handleCopyCommand = () => {
    navigator.clipboard.writeText(info.command).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div
      role="alert"
      aria-live="polite"
      className={cn(
        "border-b",
        isError ? "bg-destructive/10 border-destructive/20" : "bg-yellow-500/10 border-yellow-500/20",
        tier === "offline" && "animate-pulse",
      )}
    >
      {/* Main row */}
      <div className="flex items-center gap-2 px-4 py-1.5 text-xs">
        {tier === "offline" ? (
          <WifiOff className={cn("h-3.5 w-3.5 shrink-0", textColor)} />
        ) : (
          <AlertTriangle className={cn("h-3.5 w-3.5 shrink-0", textColor)} />
        )}
        <span className={cn("flex-1", textColorStrong)}>
          {info.message}
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={checkCooldown}
          className={cn("h-6 gap-1 text-xs", textColorStrong)}
          onClick={handleCheckNow}
        >
          <RefreshCw className={cn("h-3 w-3", checkCooldown && "animate-spin")} />
          {checkCooldown ? "Checking..." : "Check now"}
        </Button>
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className={cn("flex items-center gap-0.5 text-[10px] font-medium", textColor)}
        >
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          How to fix
        </button>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
          onClick={handleDismiss}
          aria-label="Dismiss degradation warning"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Expanded fix instructions */}
      {expanded && (
        <div className={cn("border-t px-4 py-2 text-[11px]", isError ? "border-destructive/10" : "border-yellow-500/10")}>
          <p className="text-muted-foreground">{info.hint}</p>
          <div className="mt-1.5 flex items-center gap-2">
            <code className={cn("flex-1 rounded bg-background/60 px-2 py-1 font-mono text-[10px]", textColor)}>
              {info.command}
            </code>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 shrink-0 p-0 text-muted-foreground hover:text-foreground"
              onClick={handleCopyCommand}
              aria-label="Copy command"
            >
              {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
