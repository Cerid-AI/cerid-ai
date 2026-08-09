// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F11 — Voice-note overlay.
 *
 * Liquid Glass modal (one of the 9 reserved surfaces). Mounted at the
 * app shell level so any pane can summon it. Hotkey: ⌘⇧V.
 *
 * Flow:
 *   1. Click record → MediaRecorder captures audio (webm/opus)
 *   2. While recording, live waveform peaks from an AnalyserNode
 *   3. Click stop → blob shipped to /sdk/v1/ingest/voice-note
 *   4. Result panel shows transcript + word count, transcribe duration
 *      pulses via .metric-value-pulse
 *
 * Visual budget:
 *   - .liquid-glass on the dialog body
 *   - .metric-pulse on the elapsed-seconds counter while recording
 *   - .cerid-press on the action buttons
 *
 * Brevity: zero-dep WebAudio API. No external waveform libs.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { Loader2, Mic, MicOff, X } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useHotkey } from "@/hooks/use-hotkey"
import { ingestVoiceNote, type VoiceNoteResponse } from "@/lib/api/voice-note"

type RecState = "idle" | "recording" | "uploading" | "done" | "error"

const PEAK_COUNT = 32

interface VoiceNoteOverlayProps {
  open: boolean
  onClose: () => void
  onArtifact?: (artifactId: string) => void
}

export function VoiceNoteOverlay({ open, onClose, onArtifact }: VoiceNoteOverlayProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="liquid-glass max-w-md border-none p-0">
        {open && <VoiceNoteInner onClose={onClose} onArtifact={onArtifact} />}
      </DialogContent>
    </Dialog>
  )
}

function VoiceNoteInner({
  onClose,
  onArtifact,
}: {
  onClose: () => void
  onArtifact?: (artifactId: string) => void
}) {
  const [state, setState] = useState<RecState>("idle")
  const [elapsed, setElapsed] = useState(0)
  const [peaks, setPeaks] = useState<number[]>(() => new Array(PEAK_COUNT).fill(0.05))
  const [result, setResult] = useState<VoiceNoteResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mediaRecRef = useRef<MediaRecorder | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const rafRef = useRef<number | null>(null)
  const tickRef = useRef<number | null>(null)
  const startedAtRef = useRef<number>(0)

  // ---------------------------------------------------------------------------
  // Cleanup
  // ---------------------------------------------------------------------------
  const teardown = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    if (tickRef.current !== null) window.clearInterval(tickRef.current)
    rafRef.current = null
    tickRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close().catch(() => undefined)
    }
    audioCtxRef.current = null
    analyserRef.current = null
    mediaRecRef.current = null
  }, [])

  useEffect(() => {
    return () => teardown()
  }, [teardown])

  // ---------------------------------------------------------------------------
  // Start / stop
  // ---------------------------------------------------------------------------
  const start = useCallback(async () => {
    setError(null)
    setResult(null)
    chunksRef.current = []
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const ctx = new AudioContext()
      audioCtxRef.current = ctx
      const src = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      analyserRef.current = analyser
      src.connect(analyser)

      const rec = new MediaRecorder(stream, { mimeType: "audio/webm" })
      mediaRecRef.current = rec
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      rec.start(250)
      startedAtRef.current = performance.now()
      setElapsed(0)
      setState("recording")

      // Elapsed seconds ticker
      tickRef.current = window.setInterval(() => {
        setElapsed(Math.floor((performance.now() - startedAtRef.current) / 1000))
      }, 250) as unknown as number

      // Peak sampler
      const buf = new Uint8Array(analyser.frequencyBinCount)
      const drawPeaks = () => {
        if (!analyserRef.current) return
        analyserRef.current.getByteFrequencyData(buf)
        const next: number[] = []
        const step = Math.max(1, Math.floor(buf.length / PEAK_COUNT))
        for (let i = 0; i < PEAK_COUNT; i++) {
          let sum = 0
          for (let j = 0; j < step; j++) sum += buf[i * step + j] ?? 0
          next.push(Math.min(1, sum / step / 255))
        }
        setPeaks(next)
        rafRef.current = requestAnimationFrame(drawPeaks)
      }
      rafRef.current = requestAnimationFrame(drawPeaks)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Microphone access denied")
      setState("error")
    }
  }, [])

  const stop = useCallback(async () => {
    if (!mediaRecRef.current) return
    setState("uploading")

    const blobPromise = new Promise<Blob>((resolve) => {
      mediaRecRef.current!.onstop = () => {
        resolve(new Blob(chunksRef.current, { type: "audio/webm" }))
      }
      mediaRecRef.current!.stop()
    })

    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    if (tickRef.current !== null) window.clearInterval(tickRef.current)

    try {
      const blob = await blobPromise
      teardown()
      const res = await ingestVoiceNote(blob)
      setResult(res)
      setState("done")
      onArtifact?.(res.artifact_id)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Upload failed")
      setState("error")
    }
  }, [onArtifact, teardown])

  return (
    <div className="rounded-xl px-6 py-5">
      <div className="mb-4 flex items-center justify-between">
        <DialogTitle className="text-base font-medium">Voice note</DialogTitle>
        <button
          type="button"
          onClick={onClose}
          className="cerid-press rounded-full p-1 text-muted-foreground hover:text-foreground"
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {state !== "done" && state !== "error" && (
        <>
          <WaveformBar peaks={peaks} active={state === "recording"} />

          <div className="my-4 flex items-baseline justify-center gap-2">
            <span
              key={elapsed}
              className={cn(
                "metric-value-pulse text-3xl font-medium tabular-nums text-foreground",
              )}
            >
              {formatElapsed(elapsed)}
            </span>
            <span className="text-sm text-muted-foreground">
              {state === "uploading" ? "transcribing…" : "elapsed"}
            </span>
          </div>

          <div className="flex justify-center pt-2">
            {state === "idle" && (
              <Button onClick={start} size="lg" className="cerid-press">
                <Mic className="mr-2 h-4 w-4" />
                Start recording
              </Button>
            )}
            {state === "recording" && (
              <Button onClick={stop} size="lg" variant="destructive" className="cerid-press">
                <MicOff className="mr-2 h-4 w-4" />
                Stop &amp; transcribe
              </Button>
            )}
            {state === "uploading" && (
              <div className="flex items-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Transcribing…
              </div>
            )}
          </div>
        </>
      )}

      {state === "done" && result && (
        <ResultView result={result} onClose={onClose} />
      )}

      {state === "error" && error && (
        <ErrorView error={error} onRetry={() => setState("idle")} />
      )}
    </div>
  )
}

function WaveformBar({ peaks, active }: { peaks: number[]; active: boolean }) {
  return (
    <div className="flex h-16 items-center justify-center gap-[3px]"> {/* drift-allowed: gap pinned to exactly match sibling bar width (3px) for a symmetric waveform rhythm; nearest Tailwind gap step (gap-1=4px) breaks the visual match */}
      {peaks.map((p, i) => (
        <span
          key={i}
          className={cn(
            "rounded-full bg-brand transition-all duration-100",
            !active && "bg-foreground/20",
          )}
          style={{ // drift-allowed: waveform bar height/opacity computed per-frame from live audio peak amplitude; no static equivalent
            width: 3,
            height: `${Math.max(3, p * 56)}px`,
            opacity: active ? 0.4 + p * 0.6 : 0.5,
          }}
        />
      ))}
    </div>
  )
}

function ResultView({
  result,
  onClose,
}: {
  result: VoiceNoteResponse
  onClose: () => void
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-center gap-2">
        <span
          key={result.transcribe_ms}
          className="metric-value-pulse text-3xl font-medium tabular-nums text-foreground"
        >
          {result.transcribe_ms}
        </span>
        <span className="text-sm text-muted-foreground">ms · {result.word_count} words</span>
      </div>

      <div className="max-h-40 overflow-y-auto rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground">
        {result.transcript}
      </div>

      <div className="flex justify-end pt-1">
        <Button onClick={onClose}>Done</Button>
      </div>
    </div>
  )
}

function ErrorView({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
        {error}
      </div>
      <div className="flex justify-end">
        <Button onClick={onRetry}>Try again</Button>
      </div>
    </div>
  )
}

function formatElapsed(seconds: number): string {
  const mm = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")
  const ss = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")
  return `${mm}:${ss}`
}

/**
 * Container that wires the ⌘⇧V hotkey and renders the overlay
 * conditionally. Drop this into the app shell once; it manages its
 * own open state via the hotkey.
 */
export function VoiceNoteOverlayHotkeyHost() {
  const [open, setOpen] = useState(false)
  useHotkey("meta+shift+v", () => setOpen(true))
  return <VoiceNoteOverlay open={open} onClose={() => setOpen(false)} />
}
