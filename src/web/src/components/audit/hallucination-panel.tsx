// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useRef, useCallback } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { ShieldOff, Shield, ShieldCheck, Loader2, ThumbsUp, ThumbsDown, ExternalLink, AlertTriangle, ChevronDown, ChevronUp, Search, X, Sparkles, Highlighter, RefreshCw } from "lucide-react"
import { submitClaimFeedback, verifySingleClaim } from "@/lib/api"
import type { HallucinationReport, HallucinationClaim, StreamingClaim } from "@/lib/types"
import { getClaimDisplayStatus, DISPLAY_STATUS_COLORS, verificationMethodLabel, verificationMethodColor, stripMarkdown } from "@/lib/verification-utils"
import { cn } from "@/lib/utils"

function VerificationMethodBadge({ method, model }: { method?: string; model?: string }) {
  const label = verificationMethodLabel(method)
  if (!label) return null
  return (
    <Badge variant="outline" className={`text-[10px] px-1 py-0 ${verificationMethodColor(method)}`} title={model ? `Verified by ${model}` : undefined}>
      {label}
    </Badge>
  )
}

/** Extract hostname from a URL for display. */
function claimHostname(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, "") } catch { return url }
}

function ClaimBadge({
  claim,
  index,
  conversationId,
  focused,
  onFocus,
  onRetry,
  retrying,
  expertVerified,
}: {
  claim: HallucinationClaim
  index: number
  conversationId?: string
  focused?: boolean
  onFocus?: (index: number | null) => void
  onRetry?: () => void
  retrying?: boolean
  expertVerified?: boolean
}) {
  const [feedback, setFeedback] = useState<"correct" | "incorrect" | null>(
    claim.user_feedback ?? null,
  )
  const [expanded, setExpanded] = useState(false)

  // Auto-expand when focused from inline annotation click
  useEffect(() => {
    if (focused) setExpanded(true)
  }, [focused])

  const displayStatus = getClaimDisplayStatus(claim.status, claim.verification_method, claim.claim_type)

  const handleFeedback = async (correct: boolean) => {
    if (!conversationId || feedback) return
    const value = correct ? "correct" : "incorrect"
    setFeedback(value as "correct" | "incorrect")
    try {
      await submitClaimFeedback(conversationId, index, correct)
    } catch {
      setFeedback(null) // Revert on error
    }
  }

  return (
    <div
      data-claim-index={index}
      className={cn(
        "flex items-start gap-2 rounded-lg border px-3 py-3 transition-all cursor-pointer",
        focused && "ring-2 ring-brand bg-brand/5",
      )}
      onClick={() => {
        setExpanded((prev) => !prev)
        onFocus?.(focused ? null : index)
      }}
    >
      <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-muted text-[10px] font-medium text-muted-foreground">
        {index + 1}
      </span>
      <Badge
        variant="outline"
        className={`shrink-0 ${DISPLAY_STATUS_COLORS[displayStatus] ?? DISPLAY_STATUS_COLORS.error}`}
      >
        {displayStatus}
      </Badge>
      {expertVerified && (
        <Badge variant="outline" className="shrink-0 border-purple-500/40 bg-purple-500/10 text-purple-400 text-[10px] px-1.5 py-0">
          <Sparkles className="mr-0.5 h-2.5 w-2.5" />
          expert
        </Badge>
      )}
      <div className="min-w-0 flex-1">
        {/* Re-verifying banner */}
        {retrying && (
          <div className="mb-2 flex items-center gap-2 rounded-md border border-purple-500/30 bg-purple-500/5 px-2.5 py-2 text-xs text-purple-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
            <span>Re-verifying with expert analysis (Grok 4)...</span>
          </div>
        )}
        {/* Compact: claim text (clamped) + method badge */}
        <p className={cn("text-sm leading-relaxed", !expanded && "line-clamp-1")}>{stripMarkdown(claim.claim)}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <VerificationMethodBadge method={claim.verification_method} model={claim.verification_model} />
          {claim.similarity > 0 && <span>({Math.round(claim.similarity * 100)}% match)</span>}
          <span className="inline-flex items-center gap-0.5 text-yellow-400">
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            {expanded ? "Hide details" : "Show details"}
          </span>
        </div>

        {/* Expanded details */}
        {expanded && (
          <div className="mt-2 space-y-2">
            {claim.source_filename && (
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <span>Source: {claim.source_filename}</span>
                {claim.source_domain && (
                  <Badge variant="outline" className="text-[10px] px-1 py-0">{claim.source_domain}</Badge>
                )}
              </div>
            )}
            {claim.source_snippet && (
              <p className="line-clamp-3 text-xs text-muted-foreground/70 italic leading-relaxed">
                &ldquo;{claim.source_snippet.slice(0, 150)}&rdquo;
              </p>
            )}
            {claim.reason && !claim.source_filename && (
              <p className="text-xs leading-relaxed text-muted-foreground">{stripMarkdown(claim.reason)}</p>
            )}
            {claim.consistency_issue && (
              <div className="flex items-start gap-1 text-xs text-amber-400">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                <span className="leading-relaxed">{stripMarkdown(claim.consistency_issue)}</span>
              </div>
            )}

            {/* References section */}
            <div className="border-t border-border/50 pt-2">
              <p className="text-[10px] font-medium text-muted-foreground mb-1">References</p>
              {claim.source_urls && claim.source_urls.length > 0 ? (
                <div className="flex flex-col gap-0.5">
                  {claim.source_urls.slice(0, 5).map((url, i) => (
                    <a
                      key={i}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-[11px] text-blue-500 hover:text-blue-400 truncate"
                    >
                      <ExternalLink className="h-2.5 w-2.5 shrink-0" />
                      {claimHostname(url)}
                    </a>
                  ))}
                </div>
              ) : (
                <a
                  href={`https://www.google.com/search?q=${encodeURIComponent(claim.claim)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] text-blue-500 hover:text-blue-400"
                >
                  <Search className="h-2.5 w-2.5" />
                  Search for references
                </a>
              )}
            </div>
          </div>
        )}
        {/* Action buttons — below claim text to avoid constraining it */}
        <div className="mt-1.5 flex items-center gap-1">
          {onRetry && !retrying && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-[11px] text-muted-foreground hover:text-purple-400 hover:bg-purple-500/10 gap-1"
              onClick={(e) => { e.stopPropagation(); onRetry() }}
              aria-label="Re-verify this claim with expert analysis"
              title="Re-verify with Grok 4 expert mode"
            >
              <Sparkles className="h-3 w-3" />
              Expert verify
            </Button>
          )}
          {conversationId && (
            <>
              <Button
                variant="ghost"
                size="icon"
                className={cn(
                  "h-6 w-6",
                  feedback === "correct" && "text-green-500",
                )}
                onClick={(e) => { e.stopPropagation(); handleFeedback(true) }}
                disabled={!!feedback}
                aria-label="Mark as correct"
              title="Help improve accuracy: mark this claim as correct"
            >
              <ThumbsUp className="h-3 w-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className={cn(
                "h-6 w-6",
                feedback === "incorrect" && "text-red-500",
              )}
              onClick={(e) => { e.stopPropagation(); handleFeedback(false) }}
              disabled={!!feedback}
              aria-label="Mark as incorrect"
              title="Help improve accuracy: mark this claim as incorrect"
            >
              <ThumbsDown className="h-3 w-3" />
            </Button>
          </>
        )}
        </div>
      </div>
    </div>
  )
}

function StreamingClaimBadge({ claim }: { claim: StreamingClaim }) {
  const rawStatus = claim.status ?? "pending"
  const displayStatus = rawStatus === "pending"
    ? (claim.claim_type === "evasion" ? "evasion" as const : "pending" as const)
    : getClaimDisplayStatus(rawStatus, claim.verification_method, claim.claim_type)

  return (
    <div className="flex items-start gap-2 rounded-lg border px-3 py-3">
      <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-muted text-[10px] font-medium text-muted-foreground">
        {claim.index + 1}
      </span>
      <Badge
        variant="outline"
        className={`shrink-0 ${DISPLAY_STATUS_COLORS[displayStatus] ?? DISPLAY_STATUS_COLORS.uncertain}`}
      >
        {displayStatus === "pending" ? (
          <span className="flex items-center gap-1">
            <Loader2 className="h-2.5 w-2.5 animate-spin" />
            verifying
          </span>
        ) : (
          displayStatus
        )}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-relaxed line-clamp-2">{stripMarkdown(claim.claim)}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {claim.source && (
            <span>Source: {claim.source}</span>
          )}
          {claim.source_domain && (
            <Badge variant="outline" className="text-[10px] px-1 py-0">{claim.source_domain}</Badge>
          )}
          <VerificationMethodBadge method={claim.verification_method} model={claim.verification_model} />
          {(claim.source_urls?.length ?? 0) > 0 && (
            <a
              href={claim.source_urls![0]}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 hover:text-blue-300"
              title="View source"
            >
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {claim.similarity != null && claim.similarity > 0 && <span>({Math.round(claim.similarity * 100)}% match)</span>}
        </div>
        {claim.source_snippet && (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/70 italic leading-relaxed">
            &ldquo;{claim.source_snippet.slice(0, 150)}&rdquo;
          </p>
        )}
        {claim.reason && !claim.source && (
          <p className="mt-1 text-xs text-muted-foreground">{stripMarkdown(claim.reason)}</p>
        )}
        {claim.consistency_issue && (
          <div className="mt-1 flex items-start gap-1 text-xs text-amber-400">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{stripMarkdown(claim.consistency_issue)}</span>
          </div>
        )}
      </div>
    </div>
  )
}

interface HallucinationPanelProps {
  /** Hallucination report data (null = no data yet). */
  report: HallucinationReport | null
  /** Whether the report is currently being fetched. */
  loading: boolean
  /** Whether the hallucination check feature is enabled. */
  featureEnabled?: boolean
  /** Conversation ID for claim feedback. */
  conversationId?: string
  /** Streaming claims for real-time display. */
  streamingClaims?: StreamingClaim[]
  /** Index of claim focused from inline annotation click. */
  focusedClaimIndex?: number | null
  /** Callback when a claim card is clicked in the panel. */
  onClaimFocus?: (index: number | null) => void
  /** Close the verification panel. */
  onClose?: () => void
  /** Expert verification toggle. */
  expertVerification?: boolean
  toggleExpertVerification?: () => void
  /** Inline markup toggle. */
  inlineMarkups?: boolean
  toggleInlineMarkups?: () => void
}

export function HallucinationPanel({
  report, loading, featureEnabled = false,
  conversationId, streamingClaims,
  focusedClaimIndex, onClaimFocus, onClose,
  expertVerification, toggleExpertVerification,
  inlineMarkups, toggleInlineMarkups,
}: HallucinationPanelProps) {
  const scrollContentRef = useRef<HTMLDivElement>(null)

  // Per-claim expert retry state
  const [retryingClaims, setRetryingClaims] = useState<Set<number>>(new Set())
  const [claimUpdates, setClaimUpdates] = useState<Map<number, Partial<HallucinationClaim>>>(new Map())
  const [expertVerifiedClaims, setExpertVerifiedClaims] = useState<Set<number>>(new Set())

  // Reset updates when report changes (new verification run)
  const reportRef = useRef(report)
  useEffect(() => {
    if (report !== reportRef.current) {
      reportRef.current = report
      setClaimUpdates(new Map())
      setRetryingClaims(new Set())
      setExpertVerifiedClaims(new Set())
    }
  }, [report])

  const handleRetryClaim = useCallback(async (index: number) => {
    const claims = report?.claims
    if (!claims?.[index] || !conversationId) return

    setRetryingClaims((prev) => new Set(prev).add(index))
    try {
      const result = await verifySingleClaim(claims[index].claim, conversationId)
      if (result) {
        setClaimUpdates((prev) => new Map(prev).set(index, result))
        setExpertVerifiedClaims((prev) => new Set(prev).add(index))
      }
    } catch { /* ignore */ }
    setRetryingClaims((prev) => { const next = new Set(prev); next.delete(index); return next })
  }, [report?.claims, conversationId])

  // Auto-scroll focused claim into view
  useEffect(() => {
    if (focusedClaimIndex == null || !scrollContentRef.current) return
    const el = scrollContentRef.current.querySelector(`[data-claim-index="${focusedClaimIndex}"]`)
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [focusedClaimIndex])
  // Feature disabled
  if (!featureEnabled) {
    return (
      <div className="flex h-full flex-col">
        <PanelHeader onClose={onClose} expertVerification={expertVerification} toggleExpertVerification={toggleExpertVerification} inlineMarkups={inlineMarkups} toggleInlineMarkups={toggleInlineMarkups} />
        <div className="flex flex-1 items-center justify-center p-3">
          <div className="flex items-center gap-2">
            <ShieldOff className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">
              Response verification is off — enable in Settings or toolbar
            </span>
          </div>
        </div>
      </div>
    )
  }

  // Streaming mode — show claims as they arrive
  if (streamingClaims && streamingClaims.length > 0) {
    const resolved = streamingClaims.filter((c) => c.status && c.status !== "pending")
    const pending = streamingClaims.filter((c) => !c.status || c.status === "pending")
    return (
      <div className="flex h-full flex-col">
        <PanelHeader onClose={onClose} expertVerification={expertVerification} toggleExpertVerification={toggleExpertVerification} inlineMarkups={inlineMarkups} toggleInlineMarkups={toggleInlineMarkups} />
        <div className="flex gap-3 px-4 py-2 text-xs">
          <span className="text-muted-foreground">
            {resolved.length}/{streamingClaims.length} verified
          </span>
          {pending.length > 0 && (
            <span className="flex items-center gap-1 text-muted-foreground">
              <Loader2 className="h-2.5 w-2.5 animate-spin" />
              {pending.length} pending
            </span>
          )}
        </div>
        <ScrollArea className="min-h-0 flex-1 px-4 pb-3">
          <div className="space-y-2">
            {streamingClaims.map((claim) => (
              <StreamingClaimBadge key={claim.index} claim={claim} />
            ))}
          </div>
        </ScrollArea>
      </div>
    )
  }

  // Loading / checking
  if (loading) {
    return (
      <div className="flex h-full flex-col">
        <PanelHeader onClose={onClose} expertVerification={expertVerification} toggleExpertVerification={toggleExpertVerification} inlineMarkups={inlineMarkups} toggleInlineMarkups={toggleInlineMarkups} />
        <div className="flex flex-1 items-center justify-center p-3">
          <div className="flex items-center gap-2">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
            <span className="text-xs text-muted-foreground">Analyzing response...</span>
          </div>
        </div>
      </div>
    )
  }

  // No claims found (clean response)
  if (!report || report.skipped || report.summary.total === 0) {
    return (
      <div className="flex h-full flex-col">
        <PanelHeader onClose={onClose} expertVerification={expertVerification} toggleExpertVerification={toggleExpertVerification} inlineMarkups={inlineMarkups} toggleInlineMarkups={toggleInlineMarkups} />
        <div className="flex flex-1 items-center justify-center p-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 shrink-0 text-green-500" />
            <span className="text-xs text-muted-foreground">No factual claims to verify</span>
          </div>
        </div>
      </div>
    )
  }

  // Claims found — full detailed view
  const { verified, unverified, uncertain } = report.summary

  // Split unverified into refuted (cross-model/web-search) and soft unverified (KB only)
  const refutedCount = report.claims.filter(
    (c) =>
      c.status === "unverified" &&
      c.claim_type !== "evasion" &&
      (c.verification_method === "cross_model" || c.verification_method === "web_search"),
  ).length
  const evasionCount = report.claims.filter((c) => c.claim_type === "evasion").length
  const softUnverifiedCount = unverified - refutedCount - evasionCount

  return (
    <div className="flex h-full flex-col">
      <PanelHeader onClose={onClose} expertVerification={expertVerification} toggleExpertVerification={toggleExpertVerification} inlineMarkups={inlineMarkups} toggleInlineMarkups={toggleInlineMarkups} />
      <div className="flex gap-3 px-4 py-2 text-xs">
        {verified > 0 && (
          <span className="text-green-400">{verified} verified</span>
        )}
        {refutedCount > 0 && (
          <span className="text-red-400">{refutedCount} refuted</span>
        )}
        {evasionCount > 0 && (
          <span className="text-orange-400">{evasionCount} evaded</span>
        )}
        {softUnverifiedCount > 0 && (
          <span className="text-yellow-400">{softUnverifiedCount} unverified</span>
        )}
        {uncertain > 0 && (
          <span className="text-muted-foreground">{uncertain} uncertain</span>
        )}
      </div>
      <ScrollArea className="min-h-0 flex-1 px-4 pb-3">
        <div className="space-y-2" ref={scrollContentRef}>
          {report.claims.map((claim, i) => {
            // Merge per-claim expert retry results
            const mergedClaim = claimUpdates.has(i) ? { ...claim, ...claimUpdates.get(i)! } : claim
            return (
              <ClaimBadge
                key={i}
                claim={mergedClaim}
                index={i}
                conversationId={conversationId}
                focused={focusedClaimIndex === i}
                onFocus={onClaimFocus}
                onRetry={() => handleRetryClaim(i)}
                retrying={retryingClaims.has(i)}
                expertVerified={expertVerifiedClaims.has(i)}
              />
            )
          })}
        </div>
      </ScrollArea>
    </div>
  )
}

function PanelHeader({ onClose, expertVerification, toggleExpertVerification, inlineMarkups, toggleInlineMarkups }: {
  onClose?: () => void
  expertVerification?: boolean
  toggleExpertVerification?: () => void
  inlineMarkups?: boolean
  toggleInlineMarkups?: () => void
}) {
  return (
    <div className="border-b px-4 py-2">
      <div className="flex items-center gap-2">
        <Shield className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-sm font-medium">Response Verification</span>
        <div className="flex-1" />
        <TooltipProvider delayDuration={0}>
          {toggleInlineMarkups && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-6 w-6", inlineMarkups && "text-brand bg-brand/10")}
                  onClick={toggleInlineMarkups}
                  aria-label={inlineMarkups ? "Hide inline markups" : "Show inline markups"}
                >
                  <Highlighter className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{inlineMarkups ? "Inline markups: ON" : "Inline markups: OFF"}</TooltipContent>
            </Tooltip>
          )}
          {toggleExpertVerification && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-6 w-6", expertVerification && "text-amber-500 bg-amber-500/10")}
                  onClick={toggleExpertVerification}
                  aria-label={expertVerification ? "Disable expert verification" : "Enable expert verification"}
                >
                  <Sparkles className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{expertVerification ? "Expert mode: ON (~15× cost)" : "Expert mode: OFF"}</TooltipContent>
            </Tooltip>
          )}
        </TooltipProvider>
        {onClose && (
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onClose} aria-label="Close verification panel">
            <X className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  )
}
