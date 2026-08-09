// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * ClaimBadge — per-claim verification badge with hover provenance.
 *
 * Renders a keyboard-focusable `<button>` wrapping a shadcn Badge with
 * variant matching the three linguistic bands:
 *   - "verified"   → green / CheckCircle icon
 *   - "partial"    → amber / Minus icon
 *   - "unverified" → red / XCircle icon
 *
 * Wrapped in a Radix HoverCard so hovering (or focusing) opens
 * `<ProvenancePopover>` with full detail.
 *
 * WCAG 2.1 AA: color paired with icon; aria-label includes band + count.
 */

import { CheckCircle, Minus, XCircle, type LucideIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { ProvenancePopover } from "@/components/verification/provenance-popover"
import type { ClaimVerificationFE, VerificationBand } from "@/components/verification/types"
import { deriveBand, sourceCount } from "@/components/verification/types"
import { UX_COPY } from "@/lib/ux-copy"
import { cn } from "@/lib/utils"

interface ClaimBadgeProps {
  claim: ClaimVerificationFE
  /** Callback to navigate to or open an artifact. */
  onArtifactClick?: (artifactId: string) => void
}

const BAND_STYLES: Record<
  VerificationBand,
  { badge: string; icon: string; label: (n: number) => string; ariaLabel: (n: number) => string }
> = {
  verified: {
    badge:
      "border-green-500/40 bg-green-500/10 text-green-700 dark:text-green-400 hover:bg-green-500/20",
    icon: "text-green-600 dark:text-green-400",
    label: (n) => UX_COPY.verification.verified(n),
    ariaLabel: (n) => UX_COPY.verification.ariaVerified(n),
  },
  partial: {
    badge:
      "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400 hover:bg-amber-500/20",
    icon: "text-amber-600 dark:text-amber-400",
    label: () => UX_COPY.verification.partial,
    ariaLabel: () => UX_COPY.verification.ariaPartial,
  },
  unverified: {
    badge:
      "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-400 hover:bg-red-500/20",
    icon: "text-red-600 dark:text-red-400",
    label: () => UX_COPY.verification.noSource,
    ariaLabel: () => UX_COPY.verification.ariaUnverified,
  },
}

const BAND_ICONS: Record<VerificationBand, LucideIcon> = {
  verified: CheckCircle,
  partial: Minus,
  unverified: XCircle,
}

export function ClaimBadge({ claim, onArtifactClick }: ClaimBadgeProps) {
  const band = deriveBand(claim)
  const n = sourceCount(claim)
  const styles = BAND_STYLES[band]
  const Icon = BAND_ICONS[band]

  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        {/*
         * The trigger must be a focusable element so the HoverCard
         * also opens on keyboard focus (Tab key).
         * HoverCardTrigger renders its child and attaches onFocus/onBlur
         * to open/close the card — a <button> satisfies both pointer
         * and keyboard reachability.
         */}
        <button
          type="button"
          className="inline-flex cursor-default focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 rounded-full"
          aria-label={styles.ariaLabel(n)}
          data-verification-band={band}
        >
          {/* Animation lives on the inner Badge (key=band remounts the
              Badge, not the outer <button>) so keyboard focus on the
              trigger survives the streaming→settled state transition.
              Original M-A.1 placed key on the button and lost focus. */}
          <Badge
            key={band}
            variant="outline"
            className={cn(
              "gap-1 text-label-sm px-2 py-0.5 font-medium transition-colors animate-in fade-in zoom-in-95 duration-200",
              styles.badge,
            )}
          >
            <Icon
              className={cn("h-3 w-3 shrink-0", styles.icon)}
              aria-hidden="true"
            />
            {styles.label(n)}
          </Badge>
        </button>
      </HoverCardTrigger>
      <HoverCardContent
        className="w-72 p-3"
        align="start"
        side="bottom"
      >
        <ProvenancePopover claim={claim} onArtifactClick={onArtifactClick} />
      </HoverCardContent>
    </HoverCard>
  )
}
