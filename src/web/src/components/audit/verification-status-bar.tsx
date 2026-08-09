// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState } from "react"
import {
  ShieldCheck, ShieldAlert, Loader2, ChevronDown, ChevronUp,
  CheckCircle2, XOctagon, AlertTriangle, Circle, ExternalLink, RefreshCw,
} from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { ProgressBar } from "@/components/ui/progress-bar"
import type { HallucinationReport, StreamingClaim } from "@/lib/types"
import type { VerificationPhase } from "@/hooks/use-verification-stream"
import { getClaimDisplayStatus, isTimeoutMethod, stripMarkdown, type ClaimDisplayStatus } from "@/lib/verification-utils"
import { cn, getAccuracyTier } from "@/lib/utils"

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
  /** Extraction method used ("llm" | "heuristic" | "none"). */
  extractionMethod?: string | null
  /** Streaming claims for real-time display during verification. */
  streamingClaims?: StreamingClaim[]
  /** Accumulated session-wide claims checked. */
  sessionClaimsChecked?: number
  /** Accumulated session-wide estimated verification cost in USD. */
  sessionEstCost?: number
  /** Callback when a KB artifact source is clicked. */
  onArtifactClick?: (artifactId: string) => void
  /** Credit exhaustion error message from the LLM provider. */
  creditError?: string | null
  /** Re-run verification after a stream error. Resets connection pools then retriggers. */
  onRetry?: () => void
}

/** Status icon for a single claim using display status.
 *
 * M-A.6: `isCurrent` marks the single pending claim that's actively being
 * verified — its icon gets `data-current="true"` so the `animate-pulse`
 * class draws the eye without firing on every pending row at once
 * (ui-ux-pro-max guideline #7: avoid motion-meaning collisions).
 */
function ClaimStatusIcon({
  displayStatus,
  isCurrent = false,
}: {
  displayStatus: ClaimDisplayStatus
  isCurrent?: boolean
}) {
  const pulseCls = "data-[current=true]:animate-pulse"
  switch (displayStatus) {
    case "verified":
      return <CheckCircle2 data-current={isCurrent} className={cn("h-3 w-3 shrink-0 text-green-700 dark:text-green-400", pulseCls)} />
    case "refuted":
      return <XOctagon data-current={isCurrent} className={cn("h-3 w-3 shrink-0 text-red-700 dark:text-red-400", pulseCls)} />
    case "evasion":
      return <AlertTriangle data-current={isCurrent} className={cn("h-3 w-3 shrink-0 text-orange-600 dark:text-orange-400", pulseCls)} />
    case "citation":
      return <Circle data-current={isCurrent} className={cn("h-3 w-3 shrink-0 text-purple-600 dark:text-purple-400", pulseCls)} />
    case "unverified":
      return <AlertTriangle data-current={isCurrent} className={cn("h-3 w-3 shrink-0 text-amber-600 dark:text-yellow-400", pulseCls)} />
    case "skipped":
      return <Circle data-current={isCurrent} className={cn("h-3 w-3 shrink-0 text-muted-foreground/50", pulseCls)} />
    case "pending":
      return <Loader2 data-current={isCurrent} className={cn("h-3 w-3 shrink-0 animate-spin text-muted-foreground", pulseCls)} />
    case "uncertain":
    default:
      return <Circle data-current={isCurrent} className={cn("h-3 w-3 shrink-0 text-muted-foreground", pulseCls)} />
  }
}

/** Color class for a claim display status */
function claimStatusColor(displayStatus: ClaimDisplayStatus): string {
  switch (displayStatus) {
    case "verified": return "text-green-700 dark:text-green-400"
    case "refuted": return "text-red-700 dark:text-red-400"
    case "evasion": return "text-orange-600 dark:text-orange-400"
    case "citation": return "text-purple-600 dark:text-purple-400"
    case "unverified": return "text-amber-600 dark:text-yellow-400"
    case "skipped": return "text-muted-foreground"
    case "uncertain": return "text-muted-foreground"
    default: return "text-muted-foreground"
  }
}

export function VerificationStatusBar({
  report, loading, featureEnabled,
  streamPhase, verifiedCount = 0, totalClaims = 0,
  extractionMethod, streamingClaims,
  sessionClaimsChecked = 0, sessionEstCost = 0,
  onArtifactClick, creditError, onRetry,
}: VerificationStatusBarProps) {
  const [expanded, setExpanded] = useState(false)

  if (!featureEnabled) return null

  // Streaming progress states
  if (streamPhase === "extracting") {
    return (
      <div className="border-t bg-muted/30">
        <div className="flex items-center gap-2 px-4 py-1">
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
          <span className="text-xs text-muted-foreground">Extracting claims...</span>
        </div>
      </div>
    )
  }

  if (streamPhase === "verifying" && streamingClaims) {
    return (
      <div className="border-t bg-muted/30">
        <button
          className="flex w-full items-center gap-2 px-4 py-1 text-left"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          aria-label="Toggle verification details"
        >
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
          <span className="flex-1 text-xs text-muted-foreground">
            Verifying {verifiedCount}/{totalClaims} claims
            {extractionMethod && <span className="ml-1 text-muted-foreground">({extractionMethod})</span>}
          </span>
          {/* V-P2.4: expand affordance is neutral, not amber. Amber means
              "uncertain claim" — reusing it here sends a false warning. */}
          <span className="flex items-center gap-1 text-muted-foreground transition-colors">
            <span className="text-label-sm font-medium">{expanded ? "Less" : "More"}</span>
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </span>
        </button>
        {expanded && (() => {
          // M-A.6: identify the *single* claim currently being verified —
          // the first pending one. Pulsing the icon on exactly one row at a
          // time avoids the "everything blinks" anti-pattern.
          const currentIndex = streamingClaims.find((c) => (c.status ?? "pending") === "pending")?.index ?? null
          return (
          <div className="border-t border-border/50 px-4 py-1.5">
            <ul className="space-y-0.5">
              {streamingClaims.map((c) => {
                const ds = getClaimDisplayStatus(c.status ?? "pending", c.verification_method, c.claim_type)
                return (
                  <li key={c.index} className="flex flex-col gap-0.5 text-xs">
                    <div className="flex items-start gap-1.5">
                      <ClaimStatusIcon displayStatus={ds} isCurrent={c.index === currentIndex} />
                      <span className={cn("flex-1 leading-tight", claimStatusColor(ds))}>
                        {stripMarkdown(c.claim)}
                      </span>
                      {c.claim_type === "evasion" && (
                        <span className="shrink-0 rounded bg-orange-500/15 px-1 text-label-xs text-orange-600 dark:text-orange-400">evasion</span>
                      )}
                      {c.claim_type === "citation" && (
                        <span className="shrink-0 rounded bg-purple-500/15 px-1 text-label-xs text-purple-600 dark:text-purple-400">citation</span>
                      )}
                      {c.verification_method === "cross_model" && (
                        <span className="shrink-0 rounded bg-purple-500/15 px-1 text-label-xs text-purple-600 dark:text-purple-400">cross-model</span>
                      )}
                      {c.verification_method === "web_search" && (
                        <span className="shrink-0 rounded bg-blue-500/15 px-1 text-label-xs text-blue-700 dark:text-blue-400">web search</span>
                      )}
                      {c.verification_method === "kb" && (
                        <span className="shrink-0 rounded bg-cyan-500/15 px-1 text-label-xs text-cyan-700 dark:text-cyan-400">kb</span>
                      )}
                      {isTimeoutMethod(c.verification_method) && (
                        <span className="shrink-0 rounded bg-amber-500/15 px-1 text-label-xs text-amber-700 dark:text-amber-400" title="Timed out — evidence incomplete">timed out</span>
                      )}
                      {(c.source_urls?.length ?? 0) > 0 && (
                        <a
                          href={c.source_urls![0]}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="shrink-0 text-blue-700 dark:text-blue-400 hover:text-blue-300"
                          title={c.source_urls![0]}
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                      {c.source_domain && (
                        <span className="shrink-0 rounded bg-muted px-1 text-label-xs text-muted-foreground">{c.source_domain}</span>
                      )}
                    </div>
                    {c.claim_type === "ignorance" && c.status === "unverified" && c.verification_answer && (
                      <div className="ml-[18px] rounded bg-green-500/10 px-2 py-1"> {/* drift-allowed: ml-[18px] aligns sub-content under the chevron icon column */}
                        <span className="text-label-xs font-medium text-green-700 dark:text-green-400">Found answer: </span>
                        <span className="text-label-xs leading-tight text-green-800 dark:text-green-300/80">{stripMarkdown(c.verification_answer.slice(0, 300))}</span>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
          )
        })()}
      </div>
    )
  }

  // Fallback verifying state without streaming claims
  if (streamPhase === "verifying") {
    return (
      <div className="border-t bg-muted/30">
        <div className="flex items-center gap-2 px-4 py-1">
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
          <span className="text-xs text-muted-foreground">
            Verifying {verifiedCount}/{totalClaims}...
          </span>
        </div>
      </div>
    )
  }

  // Error state — stream failed or timed out
  if (streamPhase === "error") {
    return (
      <div className="border-t bg-muted/30">
        <div className="flex items-center gap-2 px-4 py-1">
          <ShieldAlert className="h-3 w-3 shrink-0 text-yellow-500" />
          <span className="flex-1 text-xs text-muted-foreground">Verification incomplete — stream interrupted</span>
          {sessionClaimsChecked > 0 && (
            <>
              <div className="h-3 w-px shrink-0 bg-border" />
              <span className="text-xs text-muted-foreground">
                Session: {sessionClaimsChecked} facts
              </span>
            </>
          )}
          {onRetry && (
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    className="flex items-center gap-1 rounded px-1.5 py-0.5 text-label-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={onRetry}
                    aria-label="Reconnect and retry verification"
                  >
                    <RefreshCw className="h-3 w-3" />
                    Reconnect
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top">Reset connection pools and retry verification</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      </div>
    )
  }

  // Fallback loading (non-streaming) — skip if stream completed to avoid masking report
  if (loading && streamPhase !== "done") {
    return (
      <div className="border-t bg-muted/30">
        <div className="flex items-center gap-2 px-4 py-1">
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
          <span className="text-xs text-muted-foreground">Analyzing response...</span>
        </div>
      </div>
    )
  }

  // Degraded with nothing settled — the chat pane's amber banner owns the
  // messaging; rendering "Verification ready" here would contradict it.
  if (streamPhase === "degraded" && (!report || !report.summary || report.summary.total === 0)) {
    return null
  }

  // No report yet or skipped
  if (!report || report.skipped || !report.summary || report.summary.total === 0) {
    return (
      <div className="border-t bg-muted/30">
        <div className="flex items-center gap-2 px-4 py-1">
          <ShieldCheck className="h-3 w-3 shrink-0 text-green-500" />
          <span className="text-xs text-muted-foreground">
            {!report ? "Verification ready" : "No claims to verify"}
          </span>
          {/* Session metrics even when no current report */}
          {sessionClaimsChecked > 0 && (
            <>
              <div className="h-3 w-px shrink-0 bg-border" />
              <span className="text-xs text-muted-foreground">
                Session: {sessionClaimsChecked} facts &bull; ~${sessionEstCost.toFixed(4)}
              </span>
            </>
          )}
        </div>
      </div>
    )
  }

  const { verified, unverified, uncertain, total } = report.summary
  const skippedCount = report.summary?.skipped ?? 0

  // Split uncertain into timed-out (evidence gathering cut short) and
  // genuinely inconclusive so the labeling stays truthful.
  const timedOutCount = report.claims.filter(
    (c) => c.status === "uncertain" && isTimeoutMethod(c.verification_method),
  ).length
  const inconclusiveCount = Math.max(uncertain - timedOutCount, 0)

  // Split unverified into refuted (cross-model/web-search) and soft unverified (KB only)
  const refutedCount = report.claims.filter(
    (c) =>
      c.status === "unverified" &&
      c.claim_type !== "evasion" &&
      (c.verification_method === "cross_model" || c.verification_method === "web_search"),
  ).length
  const evasionCount = report.claims.filter((c) => c.claim_type === "evasion").length
  const softUnverifiedCount = unverified - refutedCount - evasionCount

  // Accuracy: only refuted claims count as failures (not soft unverified)
  const denominator = verified + refutedCount
  const accuracyPct = denominator > 0 ? Math.round((verified / denominator) * 100) : 100
  const accuracyTier = getAccuracyTier(accuracyPct / 100)

  // Shield color — refuted claims trigger the warning
  const hasRefuted = refutedCount > 0
  const shieldColor = hasRefuted ? "text-red-700 dark:text-red-400" : "text-green-700 dark:text-green-400"
  const ShieldIcon = hasRefuted ? ShieldAlert : ShieldCheck
  const hasClaims = report.claims && report.claims.length > 0

  return (
    <div className="border-t bg-muted/30">
      {/* Credit exhaustion banner */}
      {creditError && (
        <div className="flex items-center gap-2 border-b border-yellow-500/20 bg-yellow-500/10 px-4 py-1.5">
          <AlertTriangle className="h-3 w-3 shrink-0 text-amber-600 dark:text-yellow-400" />
          <span className="flex-1 text-xs text-amber-700 dark:text-yellow-300">
            Verification limited &mdash;{" "}
            <a
              href="https://openrouter.ai/settings/credits"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-yellow-200"
            >
              Add OpenRouter credits
            </a>
          </span>
          {skippedCount > 0 && (
            <span className="text-label-xs text-amber-600/70 dark:text-yellow-400/70">{skippedCount} claim{skippedCount !== 1 ? "s" : ""} skipped</span>
          )}
        </div>
      )}
      {/* Summary row — clickable to expand claims.
          V-P0.1 (revised): the row click is a pointer-only convenience; the
          dedicated expand <button> at the end is the keyboard/AT control
          (aria-expanded + label). The row deliberately carries no
          role/tabIndex — a role="button" wrapper around a focusable
          descendant trips axe nested-interactive.
          CH2: the row uses two flex children — a min-w-0 core group that
          can shrink, and a shrink-0 trailing group for session metrics +
          expand toggle — so no child forces horizontal overflow. */}
      <TooltipProvider delayDuration={300}>
      <div
        className={cn(
          "flex w-full min-w-0 items-center gap-2 px-4 py-1 text-left text-xs",
          hasClaims ? "cursor-pointer" : "cursor-default",
        )}
        onClick={() => hasClaims && setExpanded(!expanded)}
      >
        {/* Core metrics group — shrinks to fit; text children truncate rather than overflow */}
        <div data-metrics="core" className="flex min-w-0 items-center gap-3">
          <ShieldIcon className={cn("h-3 w-3 shrink-0", shieldColor)} />

          {/* Degraded stream — the counts below reflect only the claims that
              settled before the timeout. Amber matches the degraded banner. */}
          {streamPhase === "degraded" && (
            <Tooltip><TooltipTrigger asChild>
              <span className="shrink-0 rounded bg-amber-500/10 px-1 text-label-xs font-medium text-amber-700 dark:text-amber-400">partial</span>
            </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Verification timed out mid-run — counts cover only the claims that settled</p></TooltipContent></Tooltip>
          )}

          {/* Claim count — show assessed vs total when some are uncertain */}
          <span className="shrink-0 text-muted-foreground">
            {uncertain > 0 ? `${verified + unverified} of ${total}` : `${total}`} claims assessed
          </span>

          {verified > 0 && (
            <Tooltip><TooltipTrigger asChild>
              <span className="shrink-0 text-green-700 dark:text-green-400">{verified} verified</span>
            </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Claims confirmed by cross-model check or KB evidence</p></TooltipContent></Tooltip>
          )}
          {refutedCount > 0 && (
            <Tooltip><TooltipTrigger asChild>
              <span className="shrink-0 text-red-700 dark:text-red-400">{refutedCount} refuted</span>
            </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Claims actively contradicted by another model or web search</p></TooltipContent></Tooltip>
          )}
          {evasionCount > 0 && (
            <Tooltip><TooltipTrigger asChild>
              <span className="shrink-0 text-orange-600 dark:text-orange-400">{evasionCount} evaded</span>
            </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Model deflected or avoided answering directly</p></TooltipContent></Tooltip>
          )}
          {softUnverifiedCount > 0 && (
            <Tooltip><TooltipTrigger asChild>
              <span className="shrink-0 text-amber-600 dark:text-yellow-400">{softUnverifiedCount} unverified</span>
            </TooltipTrigger><TooltipContent side="top"><p className="text-xs">No matching evidence found in KB (not necessarily wrong)</p></TooltipContent></Tooltip>
          )}
          {uncertain > 0 && (
            <Tooltip><TooltipTrigger asChild>
              <span className="shrink-0 text-muted-foreground">{uncertain} uncertain</span>
            </TooltipTrigger><TooltipContent side="top"><p className="text-xs">
              {timedOutCount > 0
                ? `${timedOutCount} timed out — evidence incomplete${inconclusiveCount > 0 ? ` / ${inconclusiveCount} checked but inconclusive` : ""}`
                : "Checked but inconclusive — insufficient evidence to confirm or deny"}
            </p></TooltipContent></Tooltip>
          )}

          <div className="h-3 w-px shrink-0 bg-border" />

          {/* Accuracy bar */}
          <Tooltip><TooltipTrigger asChild>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="text-muted-foreground">Accuracy:</span>
            <ProgressBar
              pct={accuracyPct}
              label="Accuracy"
              fillClassName={accuracyTier.barColor}
              className="w-12"
            />
            <span className={cn("tabular-nums", accuracyTier.textColor)}>
              {accuracyPct}%
            </span>
          </div>
          </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Verified claims / (verified + refuted). Unverified claims are excluded.</p></TooltipContent></Tooltip>

          <div className="h-3 w-px shrink-0 bg-border" />

          {/* Coherence */}
          <Tooltip><TooltipTrigger asChild>
          <span className="flex shrink-0 items-center gap-1">
            <span className="text-muted-foreground">Coherence:</span>
            <span className={accuracyTier.textColor}>{accuracyTier.label}</span>
          </span>
          </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Excellent: 95%+ accuracy. Good: 80-94%. Fair: 60-79%. Poor: below 60%.</p></TooltipContent></Tooltip>

          {/* Extraction method — label hides at narrow widths, value stays */}
          {report.extraction_method && (
            <>
              <div className="h-3 w-px shrink-0 bg-border" />
              <span className="shrink-0 text-muted-foreground">
                <span className="hidden sm:inline">via </span>{report.extraction_method}
              </span>
            </>
          )}
        </div>

        {/* Session metrics + expand toggle — never shrink, anchored at trailing edge */}
        <div data-metrics="session" className="ml-auto flex shrink-0 items-center gap-2">
          {sessionClaimsChecked > 0 && (
            <>
              <div className="h-3 w-px shrink-0 bg-border" />
              <Tooltip><TooltipTrigger asChild>
              <span className="text-muted-foreground" title={`Session: ${sessionClaimsChecked} facts • ~$${sessionEstCost.toFixed(4)}`}>
                <span className="hidden sm:inline">Session: </span>{sessionClaimsChecked} facts &bull; ~${sessionEstCost.toFixed(4)}
              </span>
              </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Total claims checked this session and estimated LLM verification cost</p></TooltipContent></Tooltip>
            </>
          )}

          {/* Expand toggle — dedicated <button> at the end of the row.
              V-P0.1 + V-P2.4: kept neutral (text-muted-foreground) rather
              than amber so it doesn't impersonate the "uncertain claim" color. */}
          {hasClaims && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                setExpanded(!expanded)
              }}
              aria-expanded={expanded}
              aria-label="Toggle verified claims"
              className="flex items-center gap-1 rounded text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="text-label-sm font-medium">{expanded ? "Less" : "More"}</span>
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>
      </TooltipProvider>

      {/* Expanded claims list with source attribution */}
      {expanded && hasClaims && (
        <div className="border-t border-border/50 px-4 py-1.5">
          <ul className="space-y-1">
            {report.claims.map((c, i) => {
              const ds = getClaimDisplayStatus(c.status, c.verification_method, c.claim_type)
              return (
                <li key={i} className="flex flex-col gap-0.5 text-xs">
                  <div className="flex items-start gap-1.5">
                    <ClaimStatusIcon displayStatus={ds} />
                    <span className={cn("flex-1 leading-tight", claimStatusColor(ds))}>
                      {stripMarkdown(c.claim)}
                    </span>
                    {c.verification_method === "cross_model" && (
                      <span className="shrink-0 rounded bg-purple-500/15 px-1 text-label-xs text-purple-600 dark:text-purple-400">cross-model</span>
                    )}
                    {c.verification_method === "web_search" && (
                      <span className="shrink-0 rounded bg-blue-500/15 px-1 text-label-xs text-blue-700 dark:text-blue-400">web search</span>
                    )}
                    {c.verification_method === "kb" && (
                      <span className="shrink-0 rounded bg-cyan-500/15 px-1 text-label-xs text-cyan-700 dark:text-cyan-400">kb</span>
                    )}
                    {isTimeoutMethod(c.verification_method) && (
                      <span className="shrink-0 rounded bg-amber-500/15 px-1 text-label-xs text-amber-700 dark:text-amber-400" title="Timed out — evidence incomplete">timed out</span>
                    )}
                    {(c.source_urls?.length ?? 0) > 0 && c.source_urls!.slice(0, 2).map((url, ui) => {
                      let domain: string
                      try { domain = new URL(url).hostname.replace(/^www\./, "") } catch { domain = "link" }
                      return (
                        <a
                          key={ui}
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex shrink-0 items-center gap-0.5 rounded bg-blue-500/15 px-1 text-label-xs text-blue-700 dark:text-blue-400 hover:text-blue-300"
                          title={url}
                        >
                          <ExternalLink className="h-2.5 w-2.5" />
                          {domain}
                        </a>
                      )
                    })}
                    {c.source_domain && !c.source_urls?.length && (
                      <span className="shrink-0 rounded bg-muted px-1 text-label-xs text-muted-foreground">{c.source_domain}</span>
                    )}
                    {c.source_filename && c.source_artifact_id && onArtifactClick ? (
                      <button
                        className="shrink-0 text-primary/70 hover:text-primary underline decoration-dotted"
                        onClick={() => onArtifactClick(c.source_artifact_id!)}
                      >
                        {c.source_filename}
                      </button>
                    ) : c.source_filename ? (
                      <span className="shrink-0 text-muted-foreground">{c.source_filename}</span>
                    ) : null}
                    {c.similarity > 0 && (
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {Math.round(c.similarity * 100)}%
                      </span>
                    )}
                  </div>
                  {c.source_snippet && (
                    <p className="ml-[18px] line-clamp-2 leading-tight text-muted-foreground italic"> {/* drift-allowed: ml-[18px] aligns sub-content under the chevron icon column */}
                      &ldquo;{c.source_snippet.slice(0, 150)}&rdquo;
                    </p>
                  )}
                  {c.claim_type === "ignorance" && c.status === "unverified" && c.verification_answer && (
                    <div className="ml-[18px] mt-0.5 rounded bg-green-500/10 px-2 py-1"> {/* drift-allowed: ml-[18px] aligns sub-content under the chevron icon column */}
                      <span className="text-label-xs font-medium text-green-700 dark:text-green-400">Found answer: </span>
                      <span className="text-label-xs leading-tight text-green-800 dark:text-green-300/80">{stripMarkdown(c.verification_answer.slice(0, 300))}</span>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}
