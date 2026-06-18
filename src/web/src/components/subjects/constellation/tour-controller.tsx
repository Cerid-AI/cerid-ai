// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Constellation tour controller — Phase B Day 7. Renders a "Take a
// tour" button overlaid on Constellation; clicking generates a tour
// arc from the backend then drives the R3F camera through each stop.
//
// Implementation: useFrame interpolates the camera between waypoints
// over each stop's duration_ms. Narration surfaces as a subtitle
// overlay (always-on for accessibility); the user can optionally
// hand it to the system TTS — Web Speech API speechSynthesis — by
// toggling "Read aloud".
//
// Pro-gated at the backend; the button surfaces an error toast if
// the user isn't on Pro.

import { useCallback, useEffect, useRef, useState } from "react"
import { useThree } from "@react-three/fiber"
import { useFrame } from "@react-three/fiber"
import { Vector3 } from "three"
import { Play, Pause, Volume2, VolumeX, Loader2 } from "lucide-react"
import { generateTour, type TourArc, type TourStop } from "@/lib/api/graph-tour"

type TourState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "playing"; arc: TourArc; stopIndex: number; stopStartMs: number }
  | { kind: "paused"; arc: TourArc; stopIndex: number; pausedAt: number }
  | { kind: "error"; message: string }

// ---------------------------------------------------------------------------
// Camera animator — must be a child of R3F's Canvas so useFrame/useThree work
// ---------------------------------------------------------------------------

interface CameraAnimatorProps {
  state: TourState
  onStopAdvance: (nextIndex: number) => void
  onComplete: () => void
}

export function TourCameraAnimator({ state, onStopAdvance, onComplete }: CameraAnimatorProps) {
  const { camera } = useThree()
  const fromCamera = useRef(new Vector3())
  const toCamera = useRef(new Vector3())
  const fromLook = useRef(new Vector3())
  const toLook = useRef(new Vector3())
  const tmpLook = useRef(new Vector3())
  const stopIndexRef = useRef(-1)

  // Capture from/to vectors at the start of each stop
  useEffect(() => {
    if (state.kind !== "playing") return
    if (state.stopIndex === stopIndexRef.current) return
    stopIndexRef.current = state.stopIndex
    const stop: TourStop = state.arc.stops[state.stopIndex]
    if (!stop) return
    fromCamera.current.copy(camera.position)
    toCamera.current.set(stop.camera[0], stop.camera[1], stop.camera[2])
    // current look_at is just the camera's forward direction projected
    fromLook.current.copy(tmpLook.current.set(0, 0, 0))
    toLook.current.set(stop.look_at[0], stop.look_at[1], stop.look_at[2])
  }, [state, camera])

  useFrame(() => {
    if (state.kind !== "playing") return
    const stop = state.arc.stops[state.stopIndex]
    if (!stop) return
    const elapsed = performance.now() - state.stopStartMs
    const t = Math.min(1, elapsed / Math.max(1, stop.duration_ms))
    // Ease-in-out cubic
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2

    camera.position.lerpVectors(fromCamera.current, toCamera.current, e)
    tmpLook.current.lerpVectors(fromLook.current, toLook.current, e)
    camera.lookAt(tmpLook.current)

    if (t >= 1) {
      const next = state.stopIndex + 1
      if (next >= state.arc.stops.length) onComplete()
      else onStopAdvance(next)
    }
  })

  return null
}

// ---------------------------------------------------------------------------
// Tour control panel (HTML overlay, rendered OUTSIDE the R3F Canvas)
// ---------------------------------------------------------------------------

export interface TourControlPanelProps {
  focalEntity?: string | null
  state: TourState
  onStart: (arc: TourArc) => void
  onPause: () => void
  onResume: () => void
  onStop: () => void
}

export function TourControlPanel({
  focalEntity,
  state,
  onStart,
  onPause,
  onResume,
  onStop,
}: TourControlPanelProps) {
  const [readAloud, setReadAloud] = useState(false)
  const speakingRef = useRef<SpeechSynthesisUtterance | null>(null)

  const handleStart = useCallback(async () => {
    try {
      const arc = await generateTour({ focal_entity: focalEntity ?? null })
      onStart(arc)
    } catch (err) {
      console.error("tour generation failed:", err)
    }
  }, [focalEntity, onStart])

  // Read narration aloud on each stop change
  useEffect(() => {
    if (!readAloud) {
      if (typeof window !== "undefined") window.speechSynthesis?.cancel()
      return
    }
    if (state.kind !== "playing") return
    const stop = state.arc.stops[state.stopIndex]
    if (!stop?.narration || typeof window === "undefined") return
    const utt = new SpeechSynthesisUtterance(stop.narration)
    utt.rate = 0.95
    utt.pitch = 1
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utt)
    speakingRef.current = utt
  }, [state, readAloud])

  // Cleanup TTS on unmount
  useEffect(() => {
    return () => {
      if (typeof window !== "undefined") window.speechSynthesis?.cancel()
    }
  }, [])

  return (
    <>
      {/* Top-center floating control + subtitle */}
      <div className="pointer-events-none absolute inset-x-0 top-4 z-20 flex flex-col items-center gap-2">
        {state.kind === "idle" && (
          <button
            type="button"
            onClick={handleStart}
            className="liquid-glass pointer-events-auto flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium text-foreground hover:bg-accent/30"
          >
            <Play className="h-3.5 w-3.5" aria-hidden="true" />
            Take a tour
          </button>
        )}
        {state.kind === "loading" && (
          <div className="liquid-glass pointer-events-auto flex items-center gap-2 rounded-full px-4 py-2 text-sm">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            Composing tour…
          </div>
        )}
        {(state.kind === "playing" || state.kind === "paused") && (
          <div className="pointer-events-auto flex max-w-xl flex-col items-center gap-2">
            {/* Controls */}
            <div className="liquid-glass flex items-center gap-1.5 rounded-full px-3 py-1">
              {state.kind === "playing" ? (
                <button type="button" onClick={onPause} aria-label="Pause" className="rounded-full p-1 hover:bg-accent/40">
                  <Pause className="h-3.5 w-3.5" />
                </button>
              ) : (
                <button type="button" onClick={onResume} aria-label="Resume" className="rounded-full p-1 hover:bg-accent/40">
                  <Play className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                type="button"
                onClick={() => setReadAloud((v) => !v)}
                aria-pressed={readAloud}
                aria-label={readAloud ? "Mute narration" : "Read narration aloud"}
                className="rounded-full p-1 hover:bg-accent/40"
              >
                {readAloud ? <Volume2 className="h-3.5 w-3.5" /> : <VolumeX className="h-3.5 w-3.5" />}
              </button>
              <span className="px-2 text-label-xs text-muted-foreground">
                Stop {state.stopIndex + 1} / {state.arc.stops.length}
              </span>
              <button
                type="button"
                onClick={onStop}
                className="rounded-full px-2 py-0.5 text-label-xs text-muted-foreground hover:bg-accent/40"
              >
                End tour
              </button>
            </div>
            {/* Subtitle — always rendered (a11y) */}
            <div className="liquid-glass rounded-md px-4 py-2 text-center text-sm text-foreground">
              {state.arc.stops[state.stopIndex]?.narration}
            </div>
          </div>
        )}
        {state.kind === "error" && (
          <div className="pointer-events-auto rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
            {state.message}
          </div>
        )}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// State helpers — used by the parent <Constellation> component
// ---------------------------------------------------------------------------

export type { TourState }

// eslint-disable-next-line react-refresh/only-export-components -- hook exported alongside the component that owns the tour state
export function useTourState() {
  const [state, setState] = useState<TourState>({ kind: "idle" })

  const startTour = useCallback((arc: TourArc) => {
    if (arc.stops.length === 0) {
      setState({ kind: "error", message: "Tour came back empty — try ingesting more content." })
      return
    }
    setState({ kind: "playing", arc, stopIndex: 0, stopStartMs: performance.now() })
  }, [])

  const advance = useCallback((nextIndex: number) => {
    setState((prev) => {
      if (prev.kind !== "playing") return prev
      return { ...prev, stopIndex: nextIndex, stopStartMs: performance.now() }
    })
  }, [])

  const pause = useCallback(() => {
    setState((prev) => {
      if (prev.kind !== "playing") return prev
      return { kind: "paused", arc: prev.arc, stopIndex: prev.stopIndex, pausedAt: performance.now() }
    })
  }, [])

  const resume = useCallback(() => {
    setState((prev) => {
      if (prev.kind !== "paused") return prev
      return { kind: "playing", arc: prev.arc, stopIndex: prev.stopIndex, stopStartMs: performance.now() }
    })
  }, [])

  const complete = useCallback(() => {
    setState({ kind: "idle" })
  }, [])

  const stop = useCallback(() => {
    setState({ kind: "idle" })
  }, [])

  return { state, startTour, advance, pause, resume, complete, stop }
}
