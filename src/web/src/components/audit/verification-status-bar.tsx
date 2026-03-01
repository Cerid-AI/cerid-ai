// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { ShieldCheck, ShieldAlert, Loader2 } from "lucide-react"
import type { HallucinationReport } from "@/lib/types"
import type { VerificationPhase } from "@/hooks/use-verification-stream"
import { cn } from "@/lib/utils"

interface VerificationStatusBarProps {
  report: HallucinationReport | null
  loading: boolean
  featureEnabled: boolean
  /** Streaming phase for progressive display. */
  streamPhase?: VerificationPhase
  /** Claims verified so far (streaming). */
  verifiedCount?: number
  /** Total claims extracted (streaming). */
  totalClaims?: number
}

export function VerificationStatusBar({
  report, loading, featureEnabled,
  streamPhase, verifiedCount = 0, totalClaims = 0,
}: VerificationStatusBarProps) {
  if (!featureEnabled) return null

  // Streaming progress states
  if (streamPhase === "extracting") {
    return (
      <div className="flex items-center gap-2 border-t bg-muted/30 px-4 py-1">
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
        <span className="text-xs text-muted-foreground">Extracting claims...</span>
      </div>
    )
  }

  if (streamPhase === "verifying") {
    return (
      <div className="flex items-center gap-2 border-t bg-muted/30 px-4 py-1">
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
        <span className="text-xs text-muted-foreground">
          Verifying {verifiedCount}/{totalClaims}...
        </span>
      </div>
    )
  }

  // Fallback loading (non-streaming)
  if (loading) {
    return (
      <div className="flex items-center gap-2 border-t bg-muted/30 px-4 py-1">
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
        <span className="text-xs text-muted-foreground">Analyzing response...</span>
      </div>
    )
  }

  // No report yet or skipped
  if (!report || report.skipped || report.summary.total === 0) {
    return (
      <div className="flex items-center gap-2 border-t bg-muted/30 px-4 py-1">
        <ShieldCheck className="h-3 w-3 shrink-0 text-green-500" />
        <span className="text-xs text-muted-foreground">
          {!report ? "Verification ready" : "No claims to verify"}
        </span>
      </div>
    )
  }

  const { verified, unverified, uncertain, total } = report.summary
  const accuracy = total > 0 ? Math.round((verified / total) * 100) : 0

  // Coherence level
  let coherence: { label: string; color: string }
  if (accuracy >= 80) {
    coherence = { label: "High", color: "text-green-400" }
  } else if (accuracy >= 50) {
    coherence = { label: "Medium", color: "text-yellow-400" }
  } else {
    coherence = { label: "Low", color: "text-red-400" }
  }

  // Shield color based on worst state
  const shieldColor = unverified > 0
    ? "text-red-400"
    : uncertain > 0
      ? "text-yellow-400"
      : "text-green-400"

  const ShieldIcon = unverified > 0 ? ShieldAlert : ShieldCheck

  return (
    <div className="flex items-center gap-3 border-t bg-muted/30 px-4 py-1 text-xs">
      <ShieldIcon className={cn("h-3 w-3 shrink-0", shieldColor)} />

      {verified > 0 && (
        <span className="text-green-400">{verified} verified</span>
      )}
      {uncertain > 0 && (
        <span className="text-yellow-400">{uncertain} uncertain</span>
      )}
      {unverified > 0 && (
        <span className="text-red-400">{unverified} unverified</span>
      )}

      <div className="h-3 w-px shrink-0 bg-border" />

      {/* Accuracy bar */}
      <div className="flex items-center gap-1.5">
        <span className="text-muted-foreground">Accuracy:</span>
        <div className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full transition-all",
              accuracy >= 80 ? "bg-green-500" : accuracy >= 50 ? "bg-yellow-500" : "bg-red-500",
            )}
            style={{ width: `${accuracy}%` }}
          />
        </div>
        <span className={cn(
          "tabular-nums",
          accuracy >= 80 ? "text-green-400" : accuracy >= 50 ? "text-yellow-400" : "text-red-400",
        )}>
          {accuracy}%
        </span>
      </div>

      <div className="h-3 w-px shrink-0 bg-border" />

      {/* Coherence */}
      <span className="text-muted-foreground">Coherence:</span>
      <span className={coherence.color}>{coherence.label}</span>
    </div>
  )
}
