// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Timebar (A9 filter + A8 timelapse): a compact histogram of entity birth
// dates with a drag-to-select time window and a play button that sweeps a
// growth cursor over the corpus. Purely a controlled component — the window
// and playback cursor are owned by Constellation and read by the map's node
// reducer; this only renders + reports interaction.

import { useMemo, useRef } from "react"
import { Play, Pause } from "lucide-react"
import { buildTimeHistogram } from "./time-window"

// Logical SVG coordinate space; CSS scales it to the container width. d3-free:
// pointer math maps clientX → logical x via the rendered rect.
const W = 1000
const H = 40
const BUCKETS = 60

function fmt(ms: number): string {
  const d = new Date(ms)
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short" })
}

export interface TimebarProps {
  entities: readonly { created_at?: string | null }[]
  /** Selected window in epoch ms, or null for "all time". */
  window: [number, number] | null
  onWindowChange: (window: [number, number] | null) => void
  /** Playback cursor (epoch ms) or null when not playing. */
  cursor: number | null
  playing: boolean
  onTogglePlay: () => void
  /** Disables playback (prefers-reduced-motion → brush-only). */
  canPlay: boolean
}

export function Timebar({
  entities,
  window: win,
  onWindowChange,
  cursor,
  playing,
  onTogglePlay,
  canPlay,
}: TimebarProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const dragRef = useRef<{ x0: number } | null>(null)

  const hist = useMemo(() => buildTimeHistogram(entities, BUCKETS), [entities])

  // Map epoch ms ↔ logical x. Defined only when a histogram exists.
  const xOf = (ms: number) => (hist ? ((ms - hist.minMs) / (hist.maxMs - hist.minMs || 1)) * W : 0)
  const msOf = (x: number) => (hist ? hist.minMs + (x / W) * (hist.maxMs - hist.minMs || 1) : 0)

  // clientX → logical x using the rendered SVG rect (CSS scales W→pixels).
  const logicalX = (clientX: number): number => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return 0
    return Math.max(0, Math.min(W, ((clientX - rect.left) / rect.width) * W))
  }

  if (!hist) {
    return (
      <div className="pointer-events-auto rounded-lg border border-border/60 bg-card/80 px-3 py-2 text-label-xs text-muted-foreground backdrop-blur">
        No dated entities yet — timeline appears once ingestion records birth dates.
      </div>
    )
  }

  const maxCount = Math.max(1, ...hist.buckets.map((b) => b.count))
  const selX0 = win ? xOf(win[0]) : null
  const selX1 = win ? xOf(win[1]) : null
  const cursorX = cursor !== null ? xOf(cursor) : null

  const onDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (playing) return
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    dragRef.current = { x0: logicalX(e.clientX) }
  }
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current) return
    const x1 = logicalX(e.clientX)
    const a = Math.min(dragRef.current.x0, x1)
    const b = Math.max(dragRef.current.x0, x1)
    onWindowChange([msOf(a), msOf(b)])
  }
  const onUp = () => {
    const d = dragRef.current
    dragRef.current = null
    // A click (no drag) clears the window back to "all time".
    if (d && win && Math.abs(xOf(win[0]) - xOf(win[1])) < 4) onWindowChange(null)
  }

  return (
    <div className="pointer-events-auto flex items-center gap-2 rounded-lg border border-border/60 bg-card/80 px-2 py-1.5 backdrop-blur">
      <button
        type="button"
        onClick={onTogglePlay}
        disabled={!canPlay}
        aria-label={playing ? "Pause timelapse" : "Play timelapse"}
        className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent/40 disabled:opacity-40"
      >
        {playing ? <Pause className="size-3.5" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />}
      </button>
      <div className="flex flex-col">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="h-8 w-56 cursor-crosshair touch-none"
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          role="img"
          aria-label="Entity birth timeline; drag to filter by time"
        >
          {hist.buckets.map((b, i) => {
            const bw = W / hist.buckets.length
            const bh = (b.count / maxCount) * (H - 4)
            return (
              <rect
                key={i}
                x={i * bw}
                y={H - bh}
                width={Math.max(1, bw - 0.5)}
                height={bh}
                className="fill-muted-foreground/40"
              />
            )
          })}
          {selX0 !== null && selX1 !== null && (
            <rect
              x={selX0}
              y={0}
              width={Math.max(1, selX1 - selX0)}
              height={H}
              className="fill-[var(--brand)]/15 stroke-[var(--brand)]/60" // drift-allowed: brand-token brush selection
              strokeWidth={1}
            />
          )}
          {cursorX !== null && (
            <line
              x1={cursorX}
              x2={cursorX}
              y1={0}
              y2={H}
              className="stroke-[var(--brand)]" // drift-allowed: brand-token playback cursor
              strokeWidth={2}
            />
          )}
        </svg>
        <div className="mt-0.5 flex justify-between text-label-xxs text-muted-foreground">
          <span>{win ? fmt(win[0]) : fmt(hist.minMs)}</span>
          <span>{win ? fmt(win[1]) : fmt(hist.maxMs)}</span>
        </div>
      </div>
    </div>
  )
}
