// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Live activity stream for the Sources/Activity tab — Phase B Day 10.
// Combines two signals into one chronological surface:
//
//   - Active ingestion progress (/ingestion/progress, polled 3s)
//     Renders per-file rows with the 4-stage pipeline (parsing →
//     chunking → embedding → indexing) as an animated progress fill.
//
//   - Completed history (/admin/ingest-history, polled 30s)
//     Renders settled entries with source-type icon, domain badge,
//     ingested-chunks count, and timestamp.
//
// Entries fade in via a brief glow when they first appear (the
// "particle" metaphor from the spec — implemented as CSS keyframe
// rather than three.js Points so the perf cost stays trivial). New
// arrivals stack at the top; older entries scroll down out of view
// once the list exceeds the viewport.
//
// SSE: no /ingestion/progress SSE endpoint exists yet; the polling
// cadence is generous enough (3s active, 30s history) that the user
// perceives "near real-time" without burning a persistent connection.
// Upgrade path: replace the active-progress poll with an SSE listener
// once the backend ships one.

import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Upload,
  Folder,
  Mail,
  Rss,
  Bookmark,
  Clipboard,
  Globe,
  ScanLine,
  CheckCircle2,
  XCircle,
  CircleDashed,
  Loader2,
} from "lucide-react"
import { fetchIngestionProgress } from "@/lib/api/kb"
import { fetchIngestHistory } from "@/lib/api/settings"
import type { IngestionFileProgress, IngestHistoryEntry } from "@/lib/types"
import { ProgressBar } from "@/components/ui/progress-bar"
import { PaneError } from "@/components/ui/pane-error"

const ACTIVE_POLL_MS = 3_000
const HISTORY_POLL_MS = 30_000

// ---------------------------------------------------------------------------
// Source-type → icon + accent
// ---------------------------------------------------------------------------

const SOURCE_ICONS: Record<IngestHistoryEntry["source_type"], { Icon: typeof Upload; color: string }> = {
  upload: { Icon: Upload, color: "text-primary" },
  watcher: { Icon: Folder, color: "text-amber-500" },
  scanner: { Icon: ScanLine, color: "text-cyan-500" },
  webhook: { Icon: Globe, color: "text-emerald-500" },
  email: { Icon: Mail, color: "text-purple-500" },
  rss: { Icon: Rss, color: "text-orange-500" },
  bookmark: { Icon: Bookmark, color: "text-blue-500" },
  clipboard: { Icon: Clipboard, color: "text-rose-500" },
}

const STAGE_LABEL: Record<NonNullable<IngestionFileProgress["step"]>, string> = {
  parsing: "Parsing",
  chunking: "Chunking",
  embedding: "Embedding",
  indexing: "Indexing",
}

function formatRelative(iso: string): string {
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return iso
  const secs = Math.max(0, Math.floor((Date.now() - ms) / 1000))
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

// ---------------------------------------------------------------------------
// Active row — animated progress + stage label
// ---------------------------------------------------------------------------

function ActiveRow({ file }: { file: IngestionFileProgress }) {
  const isError = file.status === "error"
  const isDone = file.status === "done"
  const stageLabel = file.step ? STAGE_LABEL[file.step] ?? file.step : ""
  const pct = Math.max(0, Math.min(100, file.progress ?? 0))

  return (
    <li className="cerid-stream-glow-once rounded-lg border border-border bg-card/40 p-3 transition-shadow animate-in fade-in">
      <div className="flex items-start gap-2">
        {isError ? (
          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
        ) : isDone ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
        ) : (
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
        )}
        <div className="grow space-y-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-sm font-medium" title={file.filename}>{file.filename}</span>
            <span className="shrink-0 text-label-xs text-muted-foreground">
              {isError ? "Error" : stageLabel} {isDone ? "" : `· ${pct}%`}
            </span>
          </div>
          {!isDone && !isError && (
            <ProgressBar pct={pct} size="sm" />
          )}
          {file.error && (
            <div className="text-label-xs text-destructive">{file.error}</div>
          )}
        </div>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// History row — settled entry
// ---------------------------------------------------------------------------

function HistoryRow({ entry, isFresh }: { entry: IngestHistoryEntry; isFresh: boolean }) {
  const meta = SOURCE_ICONS[entry.source_type] ?? { Icon: Upload, color: "text-muted-foreground" }
  const { Icon, color } = meta
  return (
    <li
      className="flex items-start gap-2 rounded-md border border-transparent px-2 py-1.5 transition-colors hover:bg-accent/40"
      style={isFresh ? { animation: "cerid-stream-glow 1.6s ease-out 1" } : undefined}
    >
      <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${color}`} aria-hidden="true" />
      <div className="grow space-y-0.5">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm" title={entry.filename}>{entry.filename}</span>
          <span className="shrink-0 text-label-xxs text-muted-foreground">
            {formatRelative(entry.timestamp)}
          </span>
        </div>
        <div className="flex items-center gap-2 text-label-xxs text-muted-foreground">
          <span>{entry.source_type}</span>
          {entry.domain && <span>· {entry.domain}</span>}
          {entry.chunks > 0 && <span>· {entry.chunks} chunks</span>}
          {entry.status === "failed" && entry.error && (
            <span className="text-destructive">· {entry.error.slice(0, 40)}</span>
          )}
        </div>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SourcesActivityStream() {
  const {
    data: progress,
    isError: progressError,
    refetch: refetchProgress,
  } = useQuery({
    queryKey: ["ingestion-progress"],
    queryFn: fetchIngestionProgress,
    refetchInterval: ACTIVE_POLL_MS,
  })
  const {
    data: history,
    isError: historyError,
    refetch: refetchHistory,
  } = useQuery({
    queryKey: ["ingest-history"],
    queryFn: () => fetchIngestHistory(30),
    refetchInterval: HISTORY_POLL_MS,
  })

  // Track which history entries are newly arrived so we can flash the
  // glow on their first paint only. After ~2s we mark them "stale".
  const seenIdsRef = useRef<Set<string>>(new Set())
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set())
  useEffect(() => {
    if (!history?.items) return
    const newOnes = history.items.filter((e) => !seenIdsRef.current.has(e.id))
    if (newOnes.length === 0) return
    newOnes.forEach((e) => seenIdsRef.current.add(e.id))
    setFreshIds(new Set(newOnes.map((e) => e.id)))
    const t = window.setTimeout(() => setFreshIds(new Set()), 2_000)
    return () => window.clearTimeout(t)
  }, [history])

  const activeFiles = useMemo(() => {
    if (!progress?.files) return []
    // Sort: in-flight first (processing), then queued (pending), then settled.
    const order: Record<string, number> = { processing: 0, pending: 1, error: 2, done: 3 }
    return [...progress.files].sort(
      (a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9),
    )
  }, [progress])

  const historyEntries = history?.items ?? []

  // Cold-mount failure: nothing loaded and at least one query errored. Without
  // this branch a backend outage rendered the new-user "No activity yet"
  // onboarding card — error presented in the success voice. After a successful
  // load, react-query keeps the last data through failed polls, so this only
  // fires when there is genuinely nothing to show.
  if (activeFiles.length === 0 && historyEntries.length === 0 && (progressError || historyError)) {
    return (
      <div className="flex h-full items-center justify-center p-12">
        <div className="w-full max-w-md">
          <PaneError
            title="Couldn't load activity"
            description="The ingestion backend is unreachable."
            onRetry={() => {
              void refetchProgress()
              void refetchHistory()
            }}
          />
        </div>
      </div>
    )
  }

  if (activeFiles.length === 0 && historyEntries.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-12">
        <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
          <CircleDashed className="mx-auto mb-3 h-6 w-6 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-lg font-semibold text-foreground">No activity yet</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Upload a file, paste a URL, or set up a watched folder. New
            ingestion events appear here in near-real-time with their
            pipeline stage (parsing → chunking → embedding → indexing).
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/* Keyframe definition for the glow effect */}
      <style>{`
        @keyframes cerid-stream-glow {
          0%   { box-shadow: 0 0 0 0 rgba(90,236,203,0.55); background-color: rgba(90,236,203,0.06); }
          70%  { box-shadow: 0 0 6px 2px rgba(90,236,203,0.35); }
          100% { box-shadow: 0 0 0 0 rgba(90,236,203,0); background-color: transparent; }
        }
        .cerid-stream-glow-once {
          animation: cerid-stream-glow 1.6s ease-out 1;
        }
      `}</style>
      <div className="overflow-y-auto p-4">
        {activeFiles.length > 0 && (
          <section aria-labelledby="active-heading" className="mb-6">
            <h2
              id="active-heading"
              className="mb-2 text-label-xs font-medium uppercase tracking-wide text-muted-foreground"
            >
              Active ({activeFiles.length})
            </h2>
            <ul className="flex flex-col gap-2">
              {activeFiles.map((f, idx) => (
                <ActiveRow key={`${f.filename}-${idx}`} file={f} />
              ))}
            </ul>
          </section>
        )}

        {historyEntries.length > 0 && (
          <section aria-labelledby="recent-heading">
            <h2
              id="recent-heading"
              className="mb-2 text-label-xs font-medium uppercase tracking-wide text-muted-foreground"
            >
              Recent
            </h2>
            <ul className="flex flex-col gap-0">
              {historyEntries.map((entry) => (
                <HistoryRow key={entry.id} entry={entry} isFresh={freshIds.has(entry.id)} />
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}
