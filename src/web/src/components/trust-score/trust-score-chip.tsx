// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { ShieldCheck, ShieldAlert, ShieldX, ShieldOff, ChevronDown } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { useTrustScore } from "@/hooks/use-trust-score"
import type { ScoreBand } from "@/lib/types/trust-score"
import { getBandDisplay } from "@/lib/types/trust-score"
import { TrustScoreModal } from "./trust-score-modal"

function BandIcon({
  band,
  className,
}: {
  band: ScoreBand | null
  className?: string
}) {
  const cls = cn("h-3.5 w-3.5 shrink-0", className)
  switch (band) {
    case "high":
      return <ShieldCheck className={cls} aria-hidden="true" />
    case "medium":
      return <ShieldAlert className={cls} aria-hidden="true" />
    case "low":
      return <ShieldX className={cls} aria-hidden="true" />
    default:
      return <ShieldOff className={cls} aria-hidden="true" />
  }
}

function bandAriaLabel(score: number | null, band: ScoreBand | null): string {
  if (score === null || band === null) return "System trust score: unavailable"
  return `System trust score: ${score} of 100, ${band}`
}

/**
 * TrustScoreChip — status-bar chip showing the system trust score.
 *
 * Single-action chip (V-P1.4): click opens a Dialog with per-component tabs,
 * sparklines, and explainers. A small chevron glyph communicates the click
 * affordance. The earlier HoverCard was removed — the dual hover+click
 * pattern with no visual cue confused users about which trigger does what.
 *
 * Renders nothing on API error — operator concern, not user-visible.
 * Shows a skeleton while fetching.
 */
export function TrustScoreChip() {
  const { data, isLoading, isError } = useTrustScore()
  const [modalOpen, setModalOpen] = useState(false)

  if (isError) return null

  if (isLoading) {
    return (
      <span role="status" aria-label="Loading trust score" data-testid="trust-score-skeleton">
        <Skeleton className="h-5 w-20 rounded-full" />
      </span>
    )
  }

  // Validate shape — guards against malformed API responses in the status-bar test env
  if (!data || !Array.isArray(data.components)) return null

  const display = getBandDisplay(data.band)
  const ariaLabel = bandAriaLabel(data.score, data.band)

  return (
    <>
      <button
        type="button"
        onClick={() => setModalOpen(true)}
        aria-label={ariaLabel}
        aria-haspopup="dialog"
        data-testid="trust-score-chip"
        className={cn(
          "inline-flex cursor-pointer items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
          display.bgClass,
          display.borderClass,
          display.textClass,
        )}
      >
        <BandIcon band={data.band} />
        <span>
          Trust{" "}
          <span
            key={data.score ?? "none"}
            className="inline-block animate-in fade-in duration-200 tabular-nums"
          >
            {data.score !== null ? data.score : "—"}
          </span>
        </span>
        <ChevronDown className="size-3" aria-hidden="true" />
      </button>

      <TrustScoreModal open={modalOpen} onOpenChange={setModalOpen} data={data} />
    </>
  )
}
