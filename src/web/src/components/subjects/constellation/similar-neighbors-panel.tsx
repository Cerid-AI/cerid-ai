// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// kNN neighbors panel (B5). When a node is pinned, this floats top-right and
// lists its strongest SIMILAR_TO neighbors (ranked client-side — see
// similar-neighbors.ts), each with a similarity bar. Clicking a row re-pins to
// that entity (and flies the camera to it). Empty when the pinned node has no
// semantic neighbors yet — embeddings are a nightly job.

import { X, Waypoints } from "lucide-react"
import { EmptyState } from "@/components/ui/empty-state"
import { ProgressBar } from "@/components/ui/progress-bar"
import type { SimilarNeighbor } from "./similar-neighbors"

export interface SimilarNeighborsPanelProps {
  /** Name of the currently pinned entity — the panel's subject. */
  pinnedName: string
  /** Ranked similar neighbors (already top-N, strongest first). */
  neighbors: SimilarNeighbor[]
  /** Re-pin + fly to the neighbor at this entity index. */
  onPick: (index: number) => void
  /** Dismiss the panel. */
  onClose: () => void
}

export function SimilarNeighborsPanel({ pinnedName, neighbors, onPick, onClose }: SimilarNeighborsPanelProps) {
  return (
    <div
      className="absolute right-3 top-3 z-20 w-64"
      role="region"
      aria-label={`Semantic neighbors of ${pinnedName}`}
    >
      {/* liquid-glass forces position:relative, so the absolute wrapper above
          owns the placement (liquid-glass can't be the positioned element). */}
      <div className="liquid-glass flex flex-col gap-2 rounded-xl p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <Waypoints className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span className="truncate text-label-xs font-medium text-muted-foreground">
            Similar to <span className="text-foreground">{pinnedName}</span>
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close similar neighbors"
          className="rounded-full p-0.5 text-muted-foreground hover:bg-accent/40"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {neighbors.length === 0 ? (
        <EmptyState
          icon={Waypoints}
          title="No semantic neighbors yet"
          description="Embeddings compute nightly — similar entities surface as your corpus grows."
        />
      ) : (
        <ul className="flex flex-col gap-0.5">
          {neighbors.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => onPick(n.index)}
                aria-label={`Focus ${n.name}`}
                className="flex w-full flex-col gap-1 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-accent/30"
              >
                <span className="truncate text-label-xs text-foreground">{n.name}</span>
                <ProgressBar
                  pct={Math.round(n.normScore * 100)}
                  size="sm"
                  label={`${n.name} similarity`}
                  fillClassName="bg-[var(--brand)]" // drift-allowed: token-routed brand fill on the similarity bar
                />
              </button>
            </li>
          ))}
        </ul>
      )}
      </div>
    </div>
  )
}
