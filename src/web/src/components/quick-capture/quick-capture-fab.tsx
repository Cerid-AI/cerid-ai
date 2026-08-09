// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Quick-capture FAB — Phase B Day 12. Floating action button visible
// from every pane that opens a modal for fast knowledge ingestion via:
//   - Drop (file drag-target)
//   - Paste (text or image from clipboard)
//   - URL (paste a link to ingest)
//   - Manual note entry
//
// Bound to Cmd-Shift-N / Ctrl-Shift-N globally so users can capture
// without leaving Chat or another pane.
//
// v1 ships the UI; the actual ingest call wires to existing
// /upload + /ingestion/url endpoints (already in lib/api/kb.ts).

import { useCallback, useEffect, useRef, useState } from "react"
import { Plus, X, Upload, Link as LinkIcon, FileText, Loader2 } from "lucide-react"
import { uploadFile, ingestUrl } from "@/lib/api/kb"
import { withViewTransition } from "@/lib/view-transitions"

type CaptureMode = "url" | "note" | "upload"

export function QuickCaptureFab() {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<CaptureMode>("note")
  const [url, setUrl] = useState("")
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  // Global Cmd-Shift-N / Ctrl-Shift-N
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isMod = e.metaKey || e.ctrlKey
      if (isMod && e.shiftKey && e.key.toLowerCase() === "n") {
        e.preventDefault()
        void withViewTransition(() => setOpen(true))
      } else if (e.key === "Escape" && open) {
        void withViewTransition(() => setOpen(false))
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [open])

  // Task 3.7 — mobile bottom-tab-bar "Capture" tab opens this same modal via
  // a decoupled custom event (no prop threading / context needed). The FAB
  // keeps owning its own `open` state; the bottom bar just fires the event.
  useEffect(() => {
    const onQuickCapture = () => { void withViewTransition(() => setOpen(true)) }
    window.addEventListener("cerid:quick-capture", onQuickCapture)
    return () => window.removeEventListener("cerid:quick-capture", onQuickCapture)
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setBusy(true)
    setStatus(`Ingesting ${file.name}…`)
    try {
      await uploadFile(file)
      setStatus(`Ingested ${file.name}`)
      window.setTimeout(() => {
        setOpen(false)
        setStatus(null)
      }, 1000)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Upload failed")
    } finally {
      setBusy(false)
    }
  }, [])

  const handleNoteSave = useCallback(async () => {
    if (!note.trim()) return
    setBusy(true)
    setStatus("Saving note…")
    try {
      // Wrap as a synthetic text file so it flows through the same
      // upload pipeline. Subsequent iterations can call a dedicated
      // /ingestion/note endpoint when one lands.
      const blob = new Blob([note], { type: "text/markdown" })
      const file = new File(
        [blob],
        `note-${new Date().toISOString().slice(0, 19).replace(/[:.]/g, "-")}.md`,
        { type: "text/markdown" },
      )
      await uploadFile(file)
      setStatus("Note saved")
      setNote("")
      window.setTimeout(() => {
        setOpen(false)
        setStatus(null)
      }, 1000)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Save failed")
    } finally {
      setBusy(false)
    }
  }, [note])

  const handleUrlIngest = useCallback(async () => {
    if (!url.trim()) return
    setBusy(true)
    setStatus(`Capturing ${url}…`)
    try {
      await ingestUrl(url)
      setStatus("Captured")
      window.setTimeout(() => {
        setOpen(false)
        setStatus(null)
        setUrl("")
      }, 1500)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "URL capture failed")
    } finally {
      setBusy(false)
    }
  }, [url])

  // Drop handler (Files dragged onto the modal)
  const onDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    const f = e.dataTransfer.files?.[0]
    if (f) handleFile(f)
  }, [handleFile])

  return (
    <>
      {/* The FAB itself — only visible when modal is closed. Hidden <md so
          it doesn't collide with the bottom-tab-bar's Capture tab, which
          opens the same modal via the cerid:quick-capture event above. */}
      {!open && (
        <button
          type="button"
          onClick={() => withViewTransition(() => setOpen(true))}
          aria-label="Quick capture"
          title="Quick capture (⌘⇧N)"
          style={{ viewTransitionName: "quick-capture-surface" }} // drift-allowed: View Transition API requires setting view-transition-name via inline style; no Tailwind utility exists
          className="fixed bottom-6 right-6 z-40 hidden h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xl transition-transform hover:scale-105 hover:bg-primary/90 md:inline-flex"
        >
          <Plus className="h-5 w-5" aria-hidden="true" />
        </button>
      )}

      {/* Modal */}
      {open && (
        // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- modal backdrop; handlers dismiss on outside-click / Escape
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Quick capture"
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
          onClick={(e) => {
            if (e.target === e.currentTarget) void withViewTransition(() => setOpen(false))
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") void withViewTransition(() => setOpen(false))
          }}
        >
          <div
            style={{ viewTransitionName: "quick-capture-surface" }} // drift-allowed: View Transition API requires setting view-transition-name via inline style; no Tailwind utility exists
            className="liquid-glass w-full max-w-lg rounded-xl"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
          >
            <div className="flex items-center gap-2 border-b px-4 py-3">
              <span className="grow text-sm font-semibold">Quick capture</span>
              <button
                type="button"
                onClick={() => withViewTransition(() => setOpen(false))}
                className="rounded p-1 text-muted-foreground hover:bg-accent/40"
                aria-label="Close"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {/* Mode tabs */}
            <div role="tablist" aria-label="Capture mode" className="flex items-center gap-1 border-b bg-card/40 px-3 py-2">
              {([
                { id: "note" as CaptureMode, label: "Note", icon: FileText },
                { id: "url" as CaptureMode, label: "URL", icon: LinkIcon },
                { id: "upload" as CaptureMode, label: "Upload", icon: Upload },
              ]).map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={mode === id}
                  onClick={() => setMode(id)}
                  className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-label-xs transition-colors ${
                    mode === id ? "bg-accent text-accent-foreground" : "text-foreground/80 hover:bg-accent/40"
                  }`}
                >
                  <Icon className="h-3 w-3" aria-hidden="true" />
                  {label}
                </button>
              ))}
            </div>

            <div className="p-4">
              {mode === "note" && (
                <div className="space-y-2">
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Type or paste a note. Drop a file here to upload instead."
                    rows={6}
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                    aria-label="Note content"
                  />
                  <div className="flex justify-end">
                    <button
                      type="button"
                      disabled={!note.trim() || busy}
                      onClick={handleNoteSave}
                      className="rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : "Save note"}
                    </button>
                  </div>
                </div>
              )}

              {mode === "url" && (
                <div className="space-y-2">
                  <input
                    type="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://…"
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                    aria-label="URL to ingest"
                  />
                  <div className="flex justify-end">
                    <button
                      type="button"
                      disabled={!url.trim() || busy}
                      onClick={handleUrlIngest}
                      className="rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : "Ingest URL"}
                    </button>
                  </div>
                </div>
              )}

              {mode === "upload" && (
                <div className="space-y-2">
                  <input
                    ref={fileInputRef}
                    type="file"
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      if (f) handleFile(f)
                    }}
                    aria-label="Choose file"
                    className="block w-full text-sm text-muted-foreground file:mr-3 file:rounded-md file:border-0 file:bg-accent file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-accent-foreground hover:file:bg-accent/80"
                  />
                  <p className="text-label-xs text-muted-foreground">
                    Or drop a file anywhere inside this modal.
                  </p>
                </div>
              )}

              {status && (
                <p className="mt-3 text-label-xs text-muted-foreground" aria-live="polite">
                  {status}
                </p>
              )}
            </div>

            <div className="border-t px-4 py-2 text-label-xs text-muted-foreground">
              <kbd className="rounded border bg-background px-1">⌘⇧N</kbd> opens this from any pane ·{" "}
              <kbd className="rounded border bg-background px-1">esc</kbd> to close
            </div>
          </div>
        </div>
      )}
    </>
  )
}
