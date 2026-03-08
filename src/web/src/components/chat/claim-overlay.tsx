// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback, useRef } from "react"
import { ExternalLink } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { DomainBadge } from "@/components/ui/domain-badge"
import { cn } from "@/lib/utils"
import type { HallucinationClaim } from "@/lib/types"
import {
  getClaimDisplayStatus,
  DISPLAY_STATUS_COLORS,
  verificationMethodLabel,
  verificationMethodColor,
  type ClaimSpan,
} from "@/lib/verification-utils"

interface ClaimOverlayProps {
  container: HTMLDivElement | null
  claims: HallucinationClaim[]
  claimSpans: ClaimSpan[]
  onArtifactClick?: (artifactId: string) => void
}

interface ActiveClaim {
  index: number
  rect: DOMRect
}

export function ClaimOverlay({ container, claims, claimSpans, onArtifactClick }: ClaimOverlayProps) {
  const [active, setActive] = useState<ActiveClaim | null>(null)
  const [hovered, setHovered] = useState<{ index: number; rect: DOMRect } | null>(null)
  const popoverRef = useRef<HTMLDivElement>(null)

  const handleMarkClick = useCallback((e: Event) => {
    const el = e.currentTarget as HTMLElement
    const idx = parseInt(el.dataset.claimIndex ?? el.dataset.ceridFootnote ?? "-1", 10)
    if (idx < 0 || idx >= claimSpans.length) return
    const rect = el.getBoundingClientRect()
    setActive((prev) => prev?.index === idx ? null : { index: idx, rect })
  }, [claimSpans.length])

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

  // Attach listeners to marks and footnotes
  useEffect(() => {
    if (!container) return
    const marks = container.querySelectorAll<HTMLElement>("[data-cerid-claim]")
    const footnotes = container.querySelectorAll<HTMLElement>("[data-cerid-footnote]")
    const all = [...marks, ...footnotes]

    for (const el of all) {
      el.addEventListener("click", handleMarkClick)
      el.addEventListener("mouseenter", handleMouseEnter)
      el.addEventListener("mouseleave", handleMouseLeave)
    }

    return () => {
      for (const el of all) {
        el.removeEventListener("click", handleMarkClick)
        el.removeEventListener("mouseenter", handleMouseEnter)
        el.removeEventListener("mouseleave", handleMouseLeave)
      }
    }
  }, [container, handleMarkClick, handleMouseEnter, handleMouseLeave])

  // Dismiss on click outside, Escape, or scroll/resize (stale DOMRect)
  useEffect(() => {
    if (!active) return
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current?.contains(e.target as Node)) return
      // Don't dismiss if clicking another mark
      const target = e.target as HTMLElement
      if (target.closest?.("[data-cerid-claim], [data-cerid-footnote]")) return
      setActive(null)
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActive(null)
    }
    const handleDismiss = () => setActive(null)
    document.addEventListener("mousedown", handleClickOutside)
    document.addEventListener("keydown", handleKeyDown)
    window.addEventListener("scroll", handleDismiss, true)
    window.addEventListener("resize", handleDismiss)
    return () => {
      document.removeEventListener("mousedown", handleClickOutside)
      document.removeEventListener("keydown", handleKeyDown)
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

  // Tooltip on hover
  if (hovered && !active) {
    const span = claimSpans[hovered.index]
    if (!span) return null
    const claim = resolveClaimData(hovered.index)
    const label = span.displayStatus + (claim?.source_domain ? ` · ${claim.source_domain}` : "")

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
  }

  // Popover on click
  if (!active) return null

  const span = claimSpans[active.index]
  if (!span) return null
  const claim = resolveClaimData(active.index)
  if (!claim) return null

  const displayStatus = getClaimDisplayStatus(claim.status, claim.verification_method, claim.claim_type)
  const methodLabel = verificationMethodLabel(claim.verification_method)
  const methodColor = verificationMethodColor(claim.verification_method)

  // Position: below mark, clamped to viewport edges (left, right, and bottom)
  const popoverLeft = Math.max(8, Math.min(active.rect.left, window.innerWidth - 320))
  const popoverHeight = 220 // approximate max popover height
  const fitsBelow = active.rect.bottom + 6 + popoverHeight < window.innerHeight
  const popoverTop = fitsBelow ? active.rect.bottom + 6 : active.rect.top - popoverHeight - 6

  return (
    <div
      ref={popoverRef}
      role="dialog"
      aria-label="Claim verification details"
      className="fixed z-50 w-[300px] rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg animate-in fade-in-0 zoom-in-95"
      style={{ left: popoverLeft, top: Math.max(8, popoverTop) }}
    >
      {/* Status badges */}
      <div className="flex items-center gap-1.5">
        <Badge
          variant="outline"
          className={cn("text-[10px]", DISPLAY_STATUS_COLORS[displayStatus] ?? DISPLAY_STATUS_COLORS.error)}
        >
          {displayStatus}
        </Badge>
        {claim.claim_type && claim.claim_type !== "factual" && (
          <Badge variant="outline" className="text-[10px] px-1 py-0">
            {claim.claim_type}
          </Badge>
        )}
      </div>

      {/* Claim text */}
      <p className="mt-2 text-xs leading-relaxed">
        {claim.claim.length > 200 ? claim.claim.slice(0, 200) + "…" : claim.claim}
      </p>

      {/* Source info */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        {claim.source_filename && (
          claim.source_artifact_id && onArtifactClick ? (
            <button
              className="text-primary hover:underline"
              onClick={() => { onArtifactClick(claim.source_artifact_id!); setActive(null) }}
            >
              {claim.source_filename}
            </button>
          ) : (
            <span>{claim.source_filename}</span>
          )
        )}
        {claim.source_domain && <DomainBadge domain={claim.source_domain} />}
        {methodLabel && (
          <Badge variant="outline" className={`text-[10px] px-1 py-0 ${methodColor}`}>
            {methodLabel}
          </Badge>
        )}
        {claim.similarity > 0 && (
          <span className="tabular-nums">{Math.round(claim.similarity * 100)}% match</span>
        )}
      </div>

      {/* Source snippet */}
      {claim.source_snippet && (
        <p className="mt-1.5 line-clamp-2 text-[11px] text-muted-foreground/70 italic leading-relaxed">
          &ldquo;{claim.source_snippet.slice(0, 150)}&rdquo;
        </p>
      )}

      {/* External source URLs */}
      {claim.source_urls && claim.source_urls.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {claim.source_urls.map((url, i) => {
            let host: string
            try { host = new URL(url).hostname.replace(/^www\./, "") } catch { host = url }
            return (
              <a
                key={i}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 text-[11px] text-blue-400 hover:text-blue-300"
              >
                <ExternalLink className="h-2.5 w-2.5" />
                {host}
              </a>
            )
          })}
        </div>
      )}

      {/* Ignorance claim: show found answer */}
      {claim.claim_type === "ignorance" && claim.status === "unverified" && claim.verification_answer && (
        <div className="mt-2 rounded bg-green-500/10 px-2 py-1.5">
          <span className="text-[10px] font-medium text-green-400">Found answer: </span>
          <span className="text-[11px] leading-tight text-green-300/80">
            {claim.verification_answer.slice(0, 300)}
          </span>
        </div>
      )}
    </div>
  )
}
