// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useRef, useState } from "react"
import { CheckCircle, MinusCircle, Circle, HelpCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { LucideIcon } from "lucide-react"

export type TrustState = "verified" | "partial" | "unverified" | "unknown"

export interface TrustBandBadgeProps {
  trust: TrustState
  /** Number of corroborating sources (shown in evidence popover). */
  corroboratingCount?: number
  /** Number of contradicting claims (shown in evidence popover). */
  contradictionCount?: number
  className?: string
}

interface TrustConfig {
  label: string
  Icon: LucideIcon
  /** CSS custom property name for the trust color token */
  tokenVar: string
  ariaDescription: string
}

const TRUST_CONFIG: Record<TrustState, TrustConfig> = {
  verified: {
    label: "verified",
    Icon: CheckCircle,
    tokenVar: "--color-map-trust-verified",
    ariaDescription: "verified by corroborating sources",
  },
  partial: {
    label: "partial",
    Icon: MinusCircle,
    tokenVar: "--color-map-trust-partial",
    ariaDescription: "partially verified with some uncertainty",
  },
  unverified: {
    label: "unverified",
    Icon: Circle,
    tokenVar: "--color-map-trust-unverified",
    ariaDescription: "not yet verified by corroborating sources",
  },
  unknown: {
    label: "unknown",
    Icon: HelpCircle,
    tokenVar: "--color-map-trust-unverified",
    ariaDescription: "trust state unknown",
  },
}

/**
 * Resolves a CSS custom property to its computed hex value from the live document.
 * Returns a fallback neutral if the token is unavailable (SSR / test environment).
 */
function resolveTrustToken(varName: string): string {
  // drift-allowed: runtime token resolution — CSS var() resolved via getComputedStyle
  // so the value is theme-reactive and never a raw hex in source.
  if (typeof document === "undefined") return "#888888"
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
  if (!raw) return "#888888"
  return raw
}

/**
 * Shared trust primitive for the Wiki Gazetteer family.
 *
 * Renders the trust band as a pill badge driven by --color-map-trust-* tokens.
 * Clicking opens a Popover showing corroborating source count and contradiction count.
 * The Atlas legend will consume this component in v2.1; for now only Wiki uses it.
 */
export function TrustBandBadge({
  trust,
  corroboratingCount,
  contradictionCount,
  className,
}: TrustBandBadgeProps) {
  const { label, Icon, tokenVar, ariaDescription } = TRUST_CONFIG[trust]
  const [color, setColor] = useState<string>("#888888")
  const resolvedRef = useRef(false)

  // Resolve the token on mount and on theme mutation. We watch the document
  // element's class (dark/light toggle) so the badge stays theme-reactive.
  useEffect(() => {
    function resolve() {
      setColor(resolveTrustToken(tokenVar))
    }
    resolve()
    resolvedRef.current = true

    const observer = new MutationObserver(resolve)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [tokenVar])

  const hasEvidenceData =
    typeof corroboratingCount === "number" || typeof contradictionCount === "number"

  const pillStyles = cn(
    "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
    "bg-card/60 transition-opacity hover:opacity-80",
    className,
  )

  if (!hasEvidenceData) {
    return (
      <span
        aria-label={`Trust: ${label} — ${ariaDescription}`}
        style={{ color, borderColor: `${color}33` }} // drift-allowed: runtime token resolution
        className={pillStyles}
      >
        <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
        {label}
      </span>
    )
  }

  // When evidence data is present, wrap in a Popover. The trigger must be a
  // <button> so Radix's aria-expanded/aria-haspopup attributes are valid.
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Trust: ${label} — ${ariaDescription}. Click to view evidence.`}
          style={{ color, borderColor: `${color}33` }} // drift-allowed: runtime token resolution
          className={cn(pillStyles, "cursor-pointer")}
        >
          <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
          {label}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-52 px-3 py-2 text-xs" align="start">
        <p className="mb-1 font-semibold text-foreground">Evidence</p>
        {typeof corroboratingCount === "number" && (
          <p className="text-muted-foreground">
            <span className="font-medium text-foreground">{corroboratingCount}</span>{" "}
            corroborating {corroboratingCount === 1 ? "source" : "sources"}
          </p>
        )}
        {typeof contradictionCount === "number" && (
          <p className={cn("text-muted-foreground", contradictionCount > 0 && "text-destructive")}>
            <span className={cn("font-medium", contradictionCount > 0 ? "text-destructive" : "text-foreground")}>
              {contradictionCount}
            </span>{" "}
            {contradictionCount === 1 ? "contradiction" : "contradictions"}
          </p>
        )}
      </PopoverContent>
    </Popover>
  )
}
