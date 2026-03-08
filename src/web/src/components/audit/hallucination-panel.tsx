// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { ShieldOff, Shield, ShieldCheck, Loader2, ThumbsUp, ThumbsDown, ExternalLink, AlertTriangle } from "lucide-react"
import { submitClaimFeedback } from "@/lib/api"
import type { HallucinationReport, HallucinationClaim, StreamingClaim } from "@/lib/types"
import { getClaimDisplayStatus, DISPLAY_STATUS_COLORS, verificationMethodLabel, verificationMethodColor } from "@/lib/verification-utils"
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

function ClaimBadge({
  claim,
  index,
  conversationId,
}: {
  claim: HallucinationClaim
  index: number
  conversationId?: string
}) {
  const [feedback, setFeedback] = useState<"correct" | "incorrect" | null>(
    claim.user_feedback ?? null,
  )

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
    <div className="flex items-start gap-2 rounded-lg border p-3">
      <Badge
        variant="outline"
        className={`shrink-0 ${DISPLAY_STATUS_COLORS[displayStatus] ?? DISPLAY_STATUS_COLORS.error}`}
      >
        {displayStatus}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-relaxed">{claim.claim}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {claim.source_filename && (
            <span>Source: {claim.source_filename}</span>
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
          {claim.similarity > 0 && <span>({Math.round(claim.similarity * 100)}% match)</span>}
        </div>
        {claim.source_snippet && (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/70 italic leading-relaxed">
            &ldquo;{claim.source_snippet.slice(0, 150)}&rdquo;
          </p>
        )}
        {claim.reason && !claim.source_filename && (
          <p className="mt-1 text-xs text-muted-foreground">{claim.reason}</p>
        )}
        {claim.consistency_issue && (
          <div className="mt-1 flex items-start gap-1 text-xs text-amber-400">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{claim.consistency_issue}</span>
          </div>
        )}
      </div>
      {conversationId && (
        <div className="flex shrink-0 gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-6 w-6",
              feedback === "correct" && "text-green-500",
            )}
            onClick={() => handleFeedback(true)}
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
            onClick={() => handleFeedback(false)}
            disabled={!!feedback}
            aria-label="Mark as incorrect"
            title="Help improve accuracy: mark this claim as incorrect"
          >
            <ThumbsDown className="h-3 w-3" />
          </Button>
        </div>
      )}
    </div>
  )
}

function StreamingClaimBadge({ claim }: { claim: StreamingClaim }) {
  const rawStatus = claim.status ?? "pending"
  const displayStatus = rawStatus === "pending"
    ? (claim.claim_type === "evasion" ? "evasion" as const : "pending" as const)
    : getClaimDisplayStatus(rawStatus, claim.verification_method, claim.claim_type)

  return (
    <div className="flex items-start gap-2 rounded-lg border p-3">
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
        <p className="text-sm leading-relaxed">{claim.claim}</p>
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
          <p className="mt-1 text-xs text-muted-foreground">{claim.reason}</p>
        )}
        {claim.consistency_issue && (
          <div className="mt-1 flex items-start gap-1 text-xs text-amber-400">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{claim.consistency_issue}</span>
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
}

export function HallucinationPanel({
  report, loading, featureEnabled = false,
  conversationId, streamingClaims,
}: HallucinationPanelProps) {
  // Feature disabled
  if (!featureEnabled) {
    return (
      <div className="flex h-full flex-col">
        <PanelHeader />
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
        <PanelHeader />
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
        <PanelHeader />
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
        <PanelHeader />
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
      <PanelHeader />
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
        <div className="space-y-2">
          {report.claims.map((claim, i) => (
            <ClaimBadge
              key={i}
              claim={claim}
              index={i}
              conversationId={conversationId}
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}

function PanelHeader() {
  return (
    <div className="border-b px-4 py-2">
      <div className="flex items-center gap-2">
        <Shield className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-sm font-medium">Response Verification</span>
      </div>
    </div>
  )
}
