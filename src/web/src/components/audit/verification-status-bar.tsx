// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import {
  ShieldCheck, ShieldAlert, Loader2, ChevronDown, ChevronUp,
  CheckCircle2, XOctagon, AlertTriangle, Circle, ExternalLink,
} from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import type { HallucinationReport, StreamingClaim } from "@/lib/types"
import type { VerificationPhase } from "@/hooks/use-verification-stream"
import { getClaimDisplayStatus, stripMarkdown, type ClaimDisplayStatus } from "@/lib/verification-utils"
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
}

/** Status icon for a single claim using display status */
function ClaimStatusIcon({ displayStatus }: { displayStatus: ClaimDisplayStatus }) {
  switch (displayStatus) {
    case "verified":
      return <CheckCircle2 className="h-3 w-3 shrink-0 text-green-400" />
    case "refuted":
      return <XOctagon className="h-3 w-3 shrink-0 text-red-400" />
    case "evasion":
      return <AlertTriangle className="h-3 w-3 shrink-0 text-orange-400" />
    case "citation":
      return <Circle className="h-3 w-3 shrink-0 text-purple-400" />
    case "unverified":
      return <AlertTriangle className="h-3 w-3 shrink-0 text-yellow-400" />
    case "pending":
      return <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
    case "uncertain":
    default:
      return <Circle className="h-3 w-3 shrink-0 text-muted-foreground" />
  }
}

/** Color class for a claim display status */
function claimStatusColor(displayStatus: ClaimDisplayStatus): string {
  switch (displayStatus) {
    case "verified": return "text-green-400"
    case "refuted": return "text-red-400"
    case "evasion": return "text-orange-400"
    case "citation": return "text-purple-400"
    case "unverified": return "text-yellow-400"
    case "uncertain": return "text-muted-foreground"
    default: return "text-muted-foreground"
  }
}

export function VerificationStatusBar({
  report, loading, featureEnabled,
  streamPhase, verifiedCount = 0, totalClaims = 0,
  extractionMethod, streamingClaims,
  sessionClaimsChecked = 0, sessionEstCost = 0,
  onArtifactClick,
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
            {extractionMethod && <span className="ml-1 text-muted-foreground/60">({extractionMethod})</span>}
          </span>
          <span className="flex items-center gap-1 text-yellow-400 transition-colors">
            <span className="text-[11px] font-medium">{expanded ? "Less" : "More"}</span>
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </span>
        </button>
        {expanded && (
          <div className="border-t border-border/50 px-4 py-1.5">
            <ul className="space-y-0.5">
              {streamingClaims.map((c) => {
                const ds = getClaimDisplayStatus(c.status ?? "pending", c.verification_method, c.claim_type)
                return (
                  <li key={c.index} className="flex flex-col gap-0.5 text-xs">
                    <div className="flex items-start gap-1.5">
                      <ClaimStatusIcon displayStatus={ds} />
                      <span className={cn("flex-1 leading-tight", claimStatusColor(ds))}>
                        {stripMarkdown(c.claim)}
                      </span>
                      {c.claim_type === "evasion" && (
                        <span className="shrink-0 rounded bg-orange-500/15 px-1 text-[10px] text-orange-400">evasion</span>
                      )}
                      {c.claim_type === "citation" && (
                        <span className="shrink-0 rounded bg-purple-500/15 px-1 text-[10px] text-purple-400">citation</span>
                      )}
                      {c.verification_method === "cross_model" && (
                        <span className="shrink-0 rounded bg-purple-500/15 px-1 text-[10px] text-purple-400">cross-model</span>
                      )}
                      {c.verification_method === "web_search" && (
                        <span className="shrink-0 rounded bg-blue-500/15 px-1 text-[10px] text-blue-400">web search</span>
                      )}
                      {c.verification_method === "kb" && (
                        <span className="shrink-0 rounded bg-cyan-500/15 px-1 text-[10px] text-cyan-400">kb</span>
                      )}
                      {(c.source_urls?.length ?? 0) > 0 && (
                        <a
                          href={c.source_urls![0]}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="shrink-0 text-blue-400 hover:text-blue-300"
                          title={c.source_urls![0]}
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                      {c.source_domain && (
                        <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">{c.source_domain}</span>
                      )}
                    </div>
                    {c.claim_type === "ignorance" && c.status === "unverified" && c.verification_answer && (
                      <div className="ml-[18px] rounded bg-green-500/10 px-2 py-1">
                        <span className="text-[10px] font-medium text-green-400">Found answer: </span>
                        <span className="text-[10px] leading-tight text-green-300/80">{stripMarkdown(c.verification_answer.slice(0, 300))}</span>
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
          <span className="text-xs text-muted-foreground">Verification incomplete — stream interrupted</span>
          {sessionClaimsChecked > 0 && (
            <>
              <div className="h-3 w-px shrink-0 bg-border" />
              <span className="text-xs text-muted-foreground/60">
                Session: {sessionClaimsChecked} facts
              </span>
            </>
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

  // No report yet or skipped
  if (!report || report.skipped || report.summary.total === 0) {
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
              <span className="text-xs text-muted-foreground/60">
                Session: {sessionClaimsChecked} facts &bull; ~${sessionEstCost.toFixed(4)}
              </span>
            </>
          )}
        </div>
      </div>
    )
  }

  const { verified, unverified, uncertain, total } = report.summary

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
  const shieldColor = hasRefuted ? "text-red-400" : "text-green-400"
  const ShieldIcon = hasRefuted ? ShieldAlert : ShieldCheck
  const hasClaims = report.claims && report.claims.length > 0

  return (
    <div className="border-t bg-muted/30">
      {/* Summary row — clickable to expand claims */}
      <TooltipProvider delayDuration={300}>
      <button
        className="flex w-full items-center gap-3 px-4 py-1 text-left text-xs"
        onClick={() => hasClaims && setExpanded(!expanded)}
        aria-expanded={hasClaims ? expanded : undefined}
        aria-label="Toggle verified claims"
        disabled={!hasClaims}
      >
        <ShieldIcon className={cn("h-3 w-3 shrink-0", shieldColor)} />

        {/* Claim count — show assessed vs total when some are uncertain */}
        <span className="text-muted-foreground">
          {uncertain > 0 ? `${verified + unverified} of ${total}` : `${total}`} claims assessed
        </span>

        {verified > 0 && (
          <Tooltip><TooltipTrigger asChild>
            <span className="text-green-400">{verified} verified</span>
          </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Claims confirmed by cross-model check or KB evidence</p></TooltipContent></Tooltip>
        )}
        {refutedCount > 0 && (
          <Tooltip><TooltipTrigger asChild>
            <span className="text-red-400">{refutedCount} refuted</span>
          </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Claims actively contradicted by another model or web search</p></TooltipContent></Tooltip>
        )}
        {evasionCount > 0 && (
          <Tooltip><TooltipTrigger asChild>
            <span className="text-orange-400">{evasionCount} evaded</span>
          </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Model deflected or avoided answering directly</p></TooltipContent></Tooltip>
        )}
        {softUnverifiedCount > 0 && (
          <Tooltip><TooltipTrigger asChild>
            <span className="text-yellow-400">{softUnverifiedCount} unverified</span>
          </TooltipTrigger><TooltipContent side="top"><p className="text-xs">No matching evidence found in KB (not necessarily wrong)</p></TooltipContent></Tooltip>
        )}
        {uncertain > 0 && (
          <Tooltip><TooltipTrigger asChild>
            <span className="text-muted-foreground/60">{uncertain} uncertain</span>
          </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Checked but inconclusive — insufficient evidence to confirm or deny</p></TooltipContent></Tooltip>
        )}

        <div className="h-3 w-px shrink-0 bg-border" />

        {/* Accuracy bar */}
        <Tooltip><TooltipTrigger asChild>
        <div className="flex items-center gap-1.5">
          <span className="text-muted-foreground">Accuracy:</span>
          <div className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
            <div
              className={cn("h-full rounded-full transition-all", accuracyTier.barColor)}
              style={{ width: `${accuracyPct}%` }}
            />
          </div>
          <span className={cn("tabular-nums", accuracyTier.textColor)}>
            {accuracyPct}%
          </span>
        </div>
        </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Verified claims / (verified + refuted). Unverified claims are excluded.</p></TooltipContent></Tooltip>

        <div className="h-3 w-px shrink-0 bg-border" />

        {/* Coherence */}
        <Tooltip><TooltipTrigger asChild>
        <span className="flex items-center gap-1">
          <span className="text-muted-foreground">Coherence:</span>
          <span className={accuracyTier.textColor}>{accuracyTier.label}</span>
        </span>
        </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Excellent: 95%+ accuracy. Good: 80-94%. Fair: 60-79%. Poor: below 60%.</p></TooltipContent></Tooltip>

        {/* Extraction method */}
        {report.extraction_method && (
          <>
            <div className="h-3 w-px shrink-0 bg-border" />
            <span className="text-muted-foreground/60">via {report.extraction_method}</span>
          </>
        )}

        {/* Session metrics */}
        {sessionClaimsChecked > 0 && (
          <>
            <div className="h-3 w-px shrink-0 bg-border" />
            <Tooltip><TooltipTrigger asChild>
            <span className="text-muted-foreground/60">
              Session: {sessionClaimsChecked} facts &bull; ~${sessionEstCost.toFixed(4)}
            </span>
            </TooltipTrigger><TooltipContent side="top"><p className="text-xs">Total claims checked this session and estimated LLM verification cost</p></TooltipContent></Tooltip>
          </>
        )}

        {/* Expand toggle */}
        <div className="flex-1" />
        {hasClaims && (
          <span className="flex items-center gap-1 text-yellow-400 transition-colors">
            <span className="text-[11px] font-medium">{expanded ? "Less" : "More"}</span>
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </span>
        )}
      </button>
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
                      <span className="shrink-0 rounded bg-purple-500/15 px-1 text-[10px] text-purple-400">cross-model</span>
                    )}
                    {c.verification_method === "web_search" && (
                      <span className="shrink-0 rounded bg-blue-500/15 px-1 text-[10px] text-blue-400">web search</span>
                    )}
                    {c.verification_method === "kb" && (
                      <span className="shrink-0 rounded bg-cyan-500/15 px-1 text-[10px] text-cyan-400">kb</span>
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
                          className="inline-flex shrink-0 items-center gap-0.5 rounded bg-blue-500/15 px-1 text-[10px] text-blue-400 hover:text-blue-300"
                          title={url}
                        >
                          <ExternalLink className="h-2.5 w-2.5" />
                          {domain}
                        </a>
                      )
                    })}
                    {c.source_domain && !c.source_urls?.length && (
                      <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">{c.source_domain}</span>
                    )}
                    {c.source_filename && c.source_artifact_id && onArtifactClick ? (
                      <button
                        className="shrink-0 text-primary/70 hover:text-primary underline decoration-dotted"
                        onClick={() => onArtifactClick(c.source_artifact_id!)}
                      >
                        {c.source_filename}
                      </button>
                    ) : c.source_filename ? (
                      <span className="shrink-0 text-muted-foreground/60">{c.source_filename}</span>
                    ) : null}
                    {c.similarity > 0 && (
                      <span className="shrink-0 tabular-nums text-muted-foreground/60">
                        {Math.round(c.similarity * 100)}%
                      </span>
                    )}
                  </div>
                  {c.source_snippet && (
                    <p className="ml-[18px] line-clamp-2 leading-tight text-muted-foreground/60 italic">
                      &ldquo;{c.source_snippet.slice(0, 150)}&rdquo;
                    </p>
                  )}
                  {c.claim_type === "ignorance" && c.status === "unverified" && c.verification_answer && (
                    <div className="ml-[18px] mt-0.5 rounded bg-green-500/10 px-2 py-1">
                      <span className="text-[10px] font-medium text-green-400">Found answer: </span>
                      <span className="text-[10px] leading-tight text-green-300/80">{stripMarkdown(c.verification_answer.slice(0, 300))}</span>
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
