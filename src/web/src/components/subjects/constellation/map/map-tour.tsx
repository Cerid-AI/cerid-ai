// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Guided graph tour on the 2D Cartographer map. Replaces the retired R3F
// tour-controller: the backend arc (POST /graph/tour/generate, Pro-gated)
// is unchanged — each stop's entity is framed by animating the sigma
// camera (via CartographerMap's tourFocus prop) while the narration
// renders as a subtitle overlay. Playback advances on each stop's
// duration_ms; pause holds the current stop.

import { useCallback, useEffect, useRef, useState } from "react"
import { Loader2, Pause, Play, X } from "lucide-react"
import { generateTour, type TourArc } from "@/lib/api/graph-tour"

export type MapTourState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "playing"; arc: TourArc; stopIndex: number }
  | { kind: "paused"; arc: TourArc; stopIndex: number }
  | { kind: "error"; message: string }

export interface MapTourFocus {
  entityId: string
  nonce: number
}

export function useMapTour() {
  const [state, setState] = useState<MapTourState>({ kind: "idle" })
  const [focus, setFocus] = useState<MapTourFocus | null>(null)
  const nonceRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const stop = useCallback(() => {
    clearTimer()
    setState({ kind: "idle" })
    setFocus(null)
  }, [clearTimer])

  const playStop = useCallback((arc: TourArc, index: number) => {
    clearTimer()
    if (index >= arc.stops.length) {
      setState({ kind: "idle" })
      setFocus(null)
      return
    }
    const tourStop = arc.stops[index]
    nonceRef.current += 1
    setFocus({ entityId: tourStop.entity_id, nonce: nonceRef.current })
    setState({ kind: "playing", arc, stopIndex: index })
    timerRef.current = setTimeout(
      () => playStop(arc, index + 1),
      Math.max(1500, tourStop.duration_ms),
    )
  }, [clearTimer])

  const start = useCallback(async () => {
    setState({ kind: "loading" })
    try {
      const arc = await generateTour({})
      if (!arc.stops.length) {
        setState({ kind: "error", message: "The tour came back empty — ingest more content first." })
        return
      }
      playStop(arc, 0)
    } catch (err) {
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Tour generation failed",
      })
    }
  }, [playStop])

  const pause = useCallback(() => {
    clearTimer()
    setState((prev) =>
      prev.kind === "playing"
        ? { kind: "paused", arc: prev.arc, stopIndex: prev.stopIndex }
        : prev,
    )
  }, [clearTimer])

  const resume = useCallback(() => {
    if (state.kind === "paused") playStop(state.arc, state.stopIndex)
  }, [state, playStop])

  useEffect(() => clearTimer, [clearTimer])

  return { state, focus, start, pause, resume, stop }
}

export function MapTourPanel({
  tour,
}: {
  tour: ReturnType<typeof useMapTour>
}) {
  const { state, start, pause, resume, stop } = tour

  if (state.kind === "idle" || state.kind === "loading") {
    return (
      <button
        type="button"
        onClick={() => void start()}
        disabled={state.kind === "loading"}
        className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-card/80 px-2.5 py-1 text-label-xs text-foreground backdrop-blur hover:bg-accent/40 disabled:opacity-60"
      >
        {state.kind === "loading" ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Play className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        Take a tour
      </button>
    )
  }

  if (state.kind === "error") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-card/90 px-2.5 py-1 text-label-xs text-destructive backdrop-blur">
        <span className="max-w-56 truncate" title={state.message}>{state.message}</span>
        <button type="button" onClick={stop} aria-label="Dismiss tour error" className="hover:opacity-70">
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    )
  }

  const currentStop = state.arc.stops[state.stopIndex]
  return (
    <div className="pointer-events-auto flex max-w-xl flex-col gap-1 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold text-foreground">
          {currentStop?.entity_name}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <span className="text-label-xs tabular-nums text-muted-foreground">
            {state.stopIndex + 1} / {state.arc.stops.length}
          </span>
          {state.kind === "playing" ? (
            <button type="button" onClick={pause} aria-label="Pause tour" className="rounded p-1 text-muted-foreground hover:bg-accent/40">
              <Pause className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          ) : (
            <button type="button" onClick={resume} aria-label="Resume tour" className="rounded p-1 text-muted-foreground hover:bg-accent/40">
              <Play className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
          <button type="button" onClick={stop} aria-label="End tour" className="rounded p-1 text-muted-foreground hover:bg-accent/40">
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
      {currentStop?.narration && (
        <p className="text-label-xs leading-relaxed text-muted-foreground" aria-live="polite">
          {currentStop.narration}
        </p>
      )}
    </div>
  )
}
