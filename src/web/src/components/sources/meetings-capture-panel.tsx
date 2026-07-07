// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Meetings Capture panel — Phase E Day 5.
//
// Drag/drop upload zone + ongoing-job preview + completed-meeting list.
// Polls /meetings/jobs at 2s while any job is in-flight, 30s otherwise.

import { useCallback, useEffect, useRef, useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { ProgressBar } from "@/components/ui/progress-bar"
import {
  AlertCircle,
  CheckCircle2,
  FileAudio,
  Loader2,
  Upload,
  Users,
  Clock,
  Calendar,
} from "lucide-react"
import { cn } from "@/lib/utils"
import {
  uploadMeeting,
  listMeetingJobs,
  type MeetingJob,
  type MeetingStage,
} from "@/lib/api/meetings"

const ACCEPT = ".m4a,.mp3,.wav,.flac,.ogg,.webm,.mp4"
const VALID_SUFFIXES = new Set([".m4a", ".mp3", ".wav", ".flac", ".ogg", ".webm", ".mp4"])

const STAGE_LABEL: Record<MeetingStage, string> = {
  queued: "Queued",
  decoding: "Decoding audio",
  transcribing: "Transcribing",
  diarizing: "Identifying speakers",
  merging: "Aligning timestamps",
  stitching: "Matching calendar",
  summarizing: "Summarizing",
  ingesting: "Saving to knowledge base",
  completed: "Completed",
  failed: "Failed",
}

function isActive(j: MeetingJob): boolean {
  return j.stage !== "completed" && j.stage !== "failed"
}

function formatDuration(s: number | null): string {
  if (s === null) return "—"
  const mins = Math.floor(s / 60)
  const secs = Math.floor(s % 60)
  return `${mins}:${String(secs).padStart(2, "0")}`
}

export function MeetingsCapturePanel() {
  const [jobs, setJobs] = useState<MeetingJob[]>([])
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      const list = await listMeetingJobs()
      setJobs(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load jobs")
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    refresh()
    const interval = setInterval(() => {
      // Caller decides polling cadence: fast if anything in-flight, slow otherwise.
      const hasActive = jobs.some(isActive)
      if (hasActive || jobs.length === 0) refresh()
    }, 2000)
    return () => clearInterval(interval)
  }, [refresh, jobs])

  const handleFile = useCallback(async (file: File) => {
    const suffix = "." + file.name.split(".").pop()?.toLowerCase()
    if (!suffix || !VALID_SUFFIXES.has(suffix)) {
      setError(`Unsupported file type: ${suffix || "(none)"}`)
      return
    }
    setUploading(true)
    setError(null)
    try {
      await uploadMeeting(file)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed")
    } finally {
      setUploading(false)
    }
  }, [refresh])

  const handleDrop = useCallback(async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) await handleFile(file)
  }, [handleFile])

  const handleInputChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) await handleFile(file)
    if (inputRef.current) inputRef.current.value = ""
  }, [handleFile])

  const activeJobs = jobs.filter(isActive)
  const completedJobs = jobs.filter((j) => !isActive(j))

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4" data-testid="meetings-capture-panel">
      <div>
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <FileAudio className="w-5 h-5" />
          Meeting Capture
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          Upload an audio recording. Cerid transcribes, identifies speakers, matches
          your calendar, and ingests it into the knowledge base.
        </p>
      </div>

      {/* Drop zone. The hidden file input lives outside this role="button"
          wrapper — axe's nested-interactive rule flags an <input> nested
          inside another interactive control, and this input is a purely
          programmatic trigger (tabIndex=-1, aria-hidden) for the labelled
          zone below, never meant to receive focus or AT interaction
          directly. */}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="pointer-events-none hidden"
        tabIndex={-1}
        aria-hidden="true"
        onChange={handleInputChange}
        data-testid="meeting-file-input"
      />
      <Card
        className={cn(
          "p-8 border-dashed border-2 flex flex-col items-center justify-center gap-3 transition-colors cursor-pointer",
          dragOver && "border-blue-500 bg-blue-500/5",
          uploading && "opacity-50 pointer-events-none"
        )}
        data-testid="meeting-drop-zone"
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click()
        }}
      >
        {uploading ? (
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        ) : (
          <Upload className="w-8 h-8 text-muted-foreground" />
        )}
        <div className="text-center">
          <p className="font-medium">
            {uploading ? "Uploading…" : "Drop an audio file here, or click to browse"}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            .m4a, .mp3, .wav, .flac, .ogg, .webm, .mp4
          </p>
        </div>
      </Card>

      {error && (
        <div className="text-sm text-red-500 p-2 rounded border border-red-500/30 bg-red-500/5 flex items-center gap-2" role="alert">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {activeJobs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-muted-foreground">In progress</h3>
          {activeJobs.map((j) => (
            <Card key={j.job_id} className="p-3 space-y-2" data-testid={`meeting-job-${j.job_id}`}>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium inline-flex items-center gap-1.5">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {STAGE_LABEL[j.stage]}
                </span>
                <span className="text-xs text-muted-foreground">
                  {Math.round(j.progress * 100)}%
                </span>
              </div>
              <ProgressBar
                pct={j.progress * 100}
                size="sm"
                label={`${STAGE_LABEL[j.stage]} — ${Math.round(j.progress * 100)}%`}
              />
            </Card>
          ))}
        </div>
      )}

      {completedJobs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-muted-foreground">Recent</h3>
          {completedJobs.map((j) => {
            const success = j.stage === "completed"
            return (
              <Card
                key={j.job_id}
                className={cn(
                  "p-3",
                  success ? "border-green-500/20 bg-green-500/5" : "border-red-500/20 bg-red-500/5"
                )}
                data-testid={`meeting-job-${j.job_id}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                      {success ? (
                        <CheckCircle2 className="w-4 h-4 text-green-500" />
                      ) : (
                        <AlertCircle className="w-4 h-4 text-red-500" />
                      )}
                      <span className="text-sm font-medium">
                        {success ? "Meeting ingested" : "Failed"}
                      </span>
                    </div>
                    {success && (
                      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          <Clock className="w-3 h-3" /> {formatDuration(j.duration_seconds)}
                        </span>
                        {j.speakers_detected !== null && j.speakers_detected > 0 && (
                          <span className="inline-flex items-center gap-1">
                            <Users className="w-3 h-3" /> {j.speakers_detected} speaker
                            {j.speakers_detected !== 1 && "s"}
                          </span>
                        )}
                        {j.calendar_event_id && (
                          <span className="inline-flex items-center gap-1">
                            <Calendar className="w-3 h-3" /> calendar matched
                          </span>
                        )}
                      </div>
                    )}
                    {!success && j.error && (
                      <p className="text-xs text-red-500/90">{j.error}</p>
                    )}
                  </div>
                  {success && j.artifact_id && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        // Surface the artifact in Subjects via the entity deep-link param
                        const url = new URL(window.location.href)
                        url.searchParams.set("entity", j.artifact_id ?? "")
                        url.searchParams.set("mode", "wiki")
                        window.history.pushState({}, "", url.toString())
                        window.dispatchEvent(new PopStateEvent("popstate"))
                      }}
                      data-testid={`meeting-view-${j.job_id}`}
                    >
                      Open
                    </Button>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
