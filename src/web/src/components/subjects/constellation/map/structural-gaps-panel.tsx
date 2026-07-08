// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Structural-gaps panel (C2) — the graph's advisory surface. Lists community
// pairs that are semantically close but weakly linked (structural holes worth
// bridging), from GET /graph/structural-gaps. Hovering a gap highlights both
// communities on the map; "Explore in chat" seeds the composer to reason about
// connecting them.

import { X, GitMerge, MessageSquare, Loader2 } from "lucide-react"
import { EmptyState } from "@/components/ui/empty-state"
import { ProgressBar } from "@/components/ui/progress-bar"
import type { StructuralGap } from "@/lib/api/graph-structural-gaps"

export interface StructuralGapsPanelProps {
  gaps: StructuralGap[]
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onClose: () => void
  /** Fires with a gap on row hover-in and null on hover-out — highlights hulls. */
  onHoverGap?: (gap: StructuralGap | null) => void
  /** Seed the chat composer to reason about bridging this gap. */
  onExplore: (gap: StructuralGap) => void
}

export function StructuralGapsPanel({
  gaps,
  isLoading,
  isError,
  errorMessage,
  onClose,
  onHoverGap,
  onExplore,
}: StructuralGapsPanelProps) {
  return (
    <div
      className="absolute left-3 top-3 z-20 flex max-h-[70%] w-72 flex-col"
      role="region"
      aria-label="Structural gaps"
    >
      {/* liquid-glass forces position:relative, so it can't be the positioned
          element — the absolute wrapper above owns the placement. */}
      <div className="liquid-glass flex min-h-0 flex-col gap-2 overflow-hidden rounded-xl p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <GitMerge className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span className="truncate text-label-xs font-medium text-foreground">Structural gaps</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close structural gaps"
          className="rounded-full p-0.5 text-muted-foreground hover:bg-accent/40"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {isLoading ? (
        <div
          role="status"
          aria-label="Loading structural gaps"
          className="flex items-center gap-2 px-1 py-4 text-label-xs text-muted-foreground"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Analyzing the graph…
        </div>
      ) : isError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-label-xs text-destructive">
          {errorMessage ?? "Failed to compute structural gaps."}
        </div>
      ) : gaps.length === 0 ? (
        <EmptyState
          icon={GitMerge}
          title="No structural gaps"
          description="Needs a few more communities with embeddings before the graph can spot topics that should connect."
        />
      ) : (
        <ul className="flex flex-col gap-2 overflow-y-auto">
          {gaps.map((gap, i) => (
            <li key={`${gap.community_a.id}|${gap.community_b.id}|${i}`}>
              <div
                role="group"
                aria-label={`${gap.community_a.label} and ${gap.community_b.label}`}
                onMouseEnter={() => onHoverGap?.(gap)}
                onMouseLeave={() => onHoverGap?.(null)}
                className="flex flex-col gap-1.5 rounded-lg border border-border/50 bg-card/40 p-2"
              >
                <div className="flex items-center gap-1 text-label-xs text-foreground">
                  <span className="truncate">{gap.community_a.label}</span>
                  <span className="shrink-0 text-muted-foreground" aria-hidden="true">↔</span>
                  <span className="truncate">{gap.community_b.label}</span>
                </div>
                <ProgressBar
                  pct={Math.round(gap.gap_score * 100)}
                  size="sm"
                  label={`Gap score between ${gap.community_a.label} and ${gap.community_b.label}`}
                  fillClassName="bg-[var(--brand)]" // drift-allowed: token-routed brand fill on the gap-score bar
                />
                <div className="text-label-xxs text-muted-foreground">
                  {Math.round(gap.semantic_similarity * 100)}% similar · {Math.round(gap.link_strength * 100)}% linked
                </div>
                {gap.bridging_candidates.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {gap.bridging_candidates.map((c) => (
                      <span
                        key={c.id}
                        className="truncate rounded-full border border-border/60 bg-card/70 px-1.5 py-0.5 text-label-xxs text-muted-foreground"
                      >
                        {c.name}
                      </span>
                    ))}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => onExplore(gap)}
                  aria-label={`Explore in chat: ${gap.community_a.label} and ${gap.community_b.label}`}
                  className="mt-0.5 flex items-center gap-1 self-start rounded-md px-1.5 py-0.5 text-label-xs text-muted-foreground transition-colors hover:bg-accent/30 hover:text-foreground"
                >
                  <MessageSquare className="h-3 w-3" aria-hidden="true" />
                  Explore in chat
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      </div>
    </div>
  )
}
