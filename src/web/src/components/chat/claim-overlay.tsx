// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from "react"
import { ExternalLink, ChevronDown, ChevronUp, Search } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { DomainBadge } from "@/components/ui/domain-badge"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import { findModel } from "@/lib/types"
import type { HallucinationClaim } from "@/lib/types"
import {
  getClaimDisplayStatus,
  DISPLAY_STATUS_COLORS,
  verificationMethodLabel,
  verificationMethodColor,
  type ClaimSpan,
} from "@/lib/verification-utils"

/** Extract a human-readable model name from a model ID string. */
function displayModelName(modelId: string | undefined): string | null {
  if (!modelId) return null
  const known = findModel(modelId)
  if (known) return known.label
  // Fallback: strip "openrouter/" prefix and extract last segment
  const segments = modelId.replace(/^openrouter\//, "").split("/")
  return segments[segments.length - 1] ?? modelId
}

interface ClaimOverlayProps {
  container: HTMLDivElement | null
  claims: HallucinationClaim[]
  claimSpans: ClaimSpan[]
  onClaimFocus?: (index: number) => void
  onArtifactClick?: (artifactId: string) => void
}

interface ActiveClaim {
  index: number
  rect: DOMRect
}

/** Extract hostname from a URL for display. */
function hostname(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, "") } catch { return url }
}

export function ClaimOverlay({ container, claims, claimSpans, onClaimFocus, onArtifactClick }: ClaimOverlayProps) {
  const [active, setActive] = useState<ActiveClaim | null>(null)
  const [hovered, setHovered] = useState<{ index: number; rect: DOMRect } | null>(null)
  const [expanded, setExpanded] = useState(false)

  const handleMarkClick = useCallback((e: Event) => {
    const el = e.currentTarget as HTMLElement
    const idx = parseInt(el.dataset.claimIndex ?? el.dataset.ceridFootnote ?? "-1", 10)
    if (idx < 0 || idx >= claimSpans.length) return
    const rect = el.getBoundingClientRect()
    setActive((prev) => prev?.index === idx ? null : { index: idx, rect })
    setExpanded(false)
    // Notify parent to focus the corresponding panel card
    onClaimFocus?.(idx)
  }, [claimSpans.length, onClaimFocus])

  const handleMouseEnter = useCallback((e: Event) => {
    if (active) return
    const el = e.currentTarget as HTMLElement
    const idx = parseInt(el.dataset.claimIndex ?? el.dataset.ceridFootnote ?? "-1", 10)
    if (idx < 0 || idx >= claimSpans.length) return
    setHovered({ index: idx, rect: el.getBoundingClientRect() })
  }, [active, claimSpans.length])

  const handleMouseLeave = useCallback(() => {
    setHovered(null)
  }, [])

  // Attach listeners: click only on footnotes [N], hover tooltip on both marks and footnotes.
  // Marks (highlighted text) should not be clickable — only the superscript reference is.
  // Use requestAnimationFrame to ensure DOM elements are settled after React render.
  useEffect(() => {
    if (!container) return

    let cancelled = false
    const rafId = requestAnimationFrame(() => {
      if (cancelled) return
      const marks = container.querySelectorAll<HTMLElement>("[data-cerid-claim]")
      const footnotes = container.querySelectorAll<HTMLElement>("[data-cerid-footnote]")

      // Footnotes: click + hover
      for (const el of footnotes) {
        el.addEventListener("click", handleMarkClick)
        el.addEventListener("mouseenter", handleMouseEnter)
        el.addEventListener("mouseleave", handleMouseLeave)
      }
      // Marks: hover tooltip only (no click — text should not be interactive)
      for (const el of marks) {
        el.addEventListener("mouseenter", handleMouseEnter)
        el.addEventListener("mouseleave", handleMouseLeave)
      }
    })

    return () => {
      cancelled = true
      cancelAnimationFrame(rafId)
      const footnotes = container.querySelectorAll<HTMLElement>("[data-cerid-footnote]")
      const marks = container.querySelectorAll<HTMLElement>("[data-cerid-claim]")
      for (const el of footnotes) {
        el.removeEventListener("click", handleMarkClick)
        el.removeEventListener("mouseenter", handleMouseEnter)
        el.removeEventListener("mouseleave", handleMouseLeave)
      }
      for (const el of marks) {
        el.removeEventListener("mouseenter", handleMouseEnter)
        el.removeEventListener("mouseleave", handleMouseLeave)
      }
    }
  }, [container, claimSpans, handleMarkClick, handleMouseEnter, handleMouseLeave])

  // Dismiss popover on scroll/resize — Radix handles Escape + click-outside
  // natively via PopoverContent's onEscapeKeyDown / onPointerDownOutside, but
  // we still kill it on scroll because the anchor rect goes stale.
  useEffect(() => {
    if (!active) return
    const handleDismiss = () => setActive(null)
    window.addEventListener("scroll", handleDismiss, true)
    window.addEventListener("resize", handleDismiss)
    return () => {
      window.removeEventListener("scroll", handleDismiss, true)
      window.removeEventListener("resize", handleDismiss)
    }
  }, [active])

  // Resolve claim data from span index
  const resolveClaimData = (spanIndex: number): HallucinationClaim | null => {
    const span = claimSpans[spanIndex]
    if (!span) return null
    return claims.find((c) => c.claim === span.claim) ?? null
  }

  // Tooltip on hover — raw fixed-position div. Cheap, no flip needed because
  // it's small and fixed at the mark's top edge.
  const tooltipNode = (() => {
    if (!hovered || active) return null
    const span = claimSpans[hovered.index]
    if (!span) return null
    const claim = resolveClaimData(hovered.index)
    const domainSuffix = claim?.verification_method === "kb" && claim?.source_domain ? ` · ${claim.source_domain}` : ""
    const label = span.displayStatus + domainSuffix

    return (
      <div
        className="pointer-events-none fixed z-50 rounded-md bg-foreground px-3 py-1.5 text-xs text-background"
        style={{
          left: hovered.rect.left + hovered.rect.width / 2,
          top: hovered.rect.top - 6,
          transform: "translate(-50%, -100%)",
        }}
      >
        {label}
      </div>
    )
  })()

  // Resolve the active claim for the popover.
  const activeSpan = active ? claimSpans[active.index] : null
  const activeClaim = active ? resolveClaimData(active.index) : null

  // Derived UI for the popover content (lift out so the JSX stays readable).
  let popoverBody: React.ReactNode = null
  if (active && activeSpan && activeClaim) {
    const displayStatus = getClaimDisplayStatus(
      activeClaim.status,
      activeClaim.verification_method,
      activeClaim.claim_type,
      activeClaim.reason,
    )
    const methodLabel = verificationMethodLabel(activeClaim.verification_method)
    const methodColor = verificationMethodColor(activeClaim.verification_method)

    popoverBody = (
      <>
        {/* Compact view: status + truncated claim + method badge */}
        <div className="flex items-center gap-1.5">
          <Badge
            variant="outline"
            className={cn("text-label-xs", DISPLAY_STATUS_COLORS[displayStatus] ?? DISPLAY_STATUS_COLORS.error)}
          >
            {displayStatus}
          </Badge>
          {activeClaim.claim_type && activeClaim.claim_type !== "factual" && (
            <Badge variant="outline" className="text-label-xs px-1 py-0">
              {activeClaim.claim_type}
            </Badge>
          )}
          {methodLabel && (
            <Badge variant="outline" className={`text-label-xs px-1 py-0 ${methodColor}`}>
              {methodLabel}
            </Badge>
          )}
          {activeClaim.verification_model?.includes("grok-4") && (
            <Badge variant="outline" className="text-label-xs px-1 py-0 bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-500/15 dark:text-indigo-400 dark:border-indigo-500/30">
              expert
            </Badge>
          )}
        </div>

        <p className="mt-2 text-xs leading-relaxed">
          {expanded
            ? activeClaim.claim
            : activeClaim.claim.length > 100 ? activeClaim.claim.slice(0, 100) + "…" : activeClaim.claim}
        </p>

        {/* Expand/collapse toggle — V-P2.4: neutral color, not amber.
            Amber is the "uncertain claim" warning; reusing it for expand
            affordances sends a false warning signal. */}
        <button
          className="mt-1.5 inline-flex items-center gap-0.5 text-label-sm text-muted-foreground hover:text-foreground"
          onClick={() => setExpanded((prev) => !prev)}
        >
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {expanded ? "Less" : "More"}
        </button>

        {/* Expanded details */}
        {expanded && (
          <>
            {/* KB-verified claims (kb or kb_nli): artifact link + snippet */}
            {(activeClaim.verification_method === "kb" || activeClaim.verification_method === "kb_nli") && (
              <>
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-label-sm text-muted-foreground">
                  {activeClaim.source_filename && (
                    activeClaim.source_artifact_id && onArtifactClick ? (
                      <button
                        className="text-primary hover:underline"
                        onClick={() => { onArtifactClick(activeClaim.source_artifact_id!); setActive(null) }}
                      >
                        {activeClaim.source_filename}
                      </button>
                    ) : (
                      <span>{activeClaim.source_filename}</span>
                    )
                  )}
                  {activeClaim.source_domain && <DomainBadge domain={activeClaim.source_domain} />}
                  {activeClaim.similarity > 0 && (
                    <span className="tabular-nums">{Math.round(activeClaim.similarity * 100)}% match</span>
                  )}
                </div>
                {activeClaim.source_snippet && (
                  <p className="mt-1.5 line-clamp-3 text-label-sm text-muted-foreground/80 italic leading-relaxed">
                    &ldquo;{activeClaim.source_snippet.slice(0, 150)}&rdquo;
                  </p>
                )}
              </>
            )}

            {/* Externally-verified claims: model + reasoning */}
            {activeClaim.verification_method !== "kb" && activeClaim.verification_method !== "kb_nli" && (
              <>
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-label-sm text-muted-foreground">
                  {displayModelName(activeClaim.verification_model) && (
                    <span className="text-muted-foreground">{displayModelName(activeClaim.verification_model)}</span>
                  )}
                  {activeClaim.similarity > 0 && (
                    <span className="tabular-nums">{Math.round(activeClaim.similarity * 100)}% confidence</span>
                  )}
                </div>
                {activeClaim.reason && (
                  <p className="mt-1.5 text-label-sm text-muted-foreground/80 leading-relaxed">
                    {activeClaim.reason.slice(0, 200)}
                  </p>
                )}
              </>
            )}

            {/* Ignorance claim: show found answer.
                V-P0.3: text-green-300/80 on bg-green-500/10 fails WCAG in
                light mode. Use a darker foreground in light, keep the
                lighter shade in dark. */}
            {activeClaim.claim_type === "ignorance" && activeClaim.status === "unverified" && activeClaim.verification_answer && (
              <div className="mt-2 rounded bg-green-500/10 px-2 py-1.5">
                <span className="text-label-xs font-medium text-green-700 dark:text-green-400">Found answer: </span>
                <span className="text-label-sm leading-tight text-green-800 dark:text-green-300/80">
                  {activeClaim.verification_answer.slice(0, 300)}
                </span>
              </div>
            )}

            {/* References section */}
            <div className="mt-2 border-t border-border/50 pt-2">
              <p className="text-label-xs font-medium text-muted-foreground mb-1">References</p>
              {activeClaim.source_urls && activeClaim.source_urls.length > 0 ? (
                <div className="flex flex-col gap-1">
                  {activeClaim.source_urls.slice(0, 5).map((url, i) => (
                    <a
                      key={i}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-label-sm text-blue-500 hover:text-blue-700 dark:text-blue-400 truncate"
                    >
                      {/* V-P2.5: bumped from h-2.5 w-2.5 (10px) to h-3 w-3 (12px) for legibility. */}
                      <ExternalLink className="h-3 w-3 shrink-0" />
                      {hostname(url)}
                    </a>
                  ))}
                </div>
              ) : (
                <a
                  href={`https://www.google.com/search?q=${encodeURIComponent(activeClaim.claim)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-label-sm text-blue-500 hover:text-blue-700 dark:text-blue-400"
                >
                  <Search className="h-3 w-3" />
                  Search for references
                </a>
              )}
            </div>
          </>
        )}
      </>
    )
  }

  return (
    <>
      {tooltipNode}
      {/*
        V-P0.2: Radix Popover replaces the previous hand-rolled fixed-position
        div + 220px magic-height flip logic. PopoverAnchor is a zero-size
        virtual element positioned at the click rect; PopoverContent handles
        collision detection, side flipping, focus management, and the
        animation — no manual ResizeObserver needed.
      */}
      <Popover
        open={active !== null && activeClaim !== null}
        onOpenChange={(open) => { if (!open) setActive(null) }}
      >
        {active && (
          <PopoverAnchor asChild>
            <span
              aria-hidden="true"
              style={{
                position: "fixed",
                left: active.rect.left,
                top: active.rect.top,
                width: active.rect.width,
                height: active.rect.height,
                pointerEvents: "none",
              }}
            />
          </PopoverAnchor>
        )}
        <PopoverContent
          side="bottom"
          align="start"
          sideOffset={6}
          collisionPadding={8}
          avoidCollisions
          className="w-[300px] rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg"
          aria-label="Claim verification details"
        >
          {popoverBody}
        </PopoverContent>
      </Popover>
    </>
  )
}
