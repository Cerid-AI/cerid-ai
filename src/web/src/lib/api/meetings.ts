// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Meeting capture API client (Phase E Day 5).

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export type MeetingStage =
  | "queued"
  | "decoding"
  | "transcribing"
  | "diarizing"
  | "merging"
  | "stitching"
  | "summarizing"
  | "ingesting"
  | "completed"
  | "failed"

export interface MeetingJob {
  job_id: string
  stage: MeetingStage
  progress: number
  started_at: number
  completed_at: number | null
  error: string | null
  artifact_id: string | null
  duration_seconds: number | null
  speakers_detected: number | null
  calendar_event_id: string | null
}

export async function uploadMeeting(file: File): Promise<{ job_id: string }> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(`${MCP_BASE}/meetings/upload`, {
    method: "POST",
    headers: mcpHeaders(),
    body: form,
  })
  if (!res.ok) throw new Error(await extractError(res, "Upload failed"))
  return res.json()
}

export async function getMeetingJob(job_id: string): Promise<MeetingJob> {
  const res = await fetch(`${MCP_BASE}/meetings/job/${job_id}`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch job"))
  return res.json()
}

export async function listMeetingJobs(): Promise<MeetingJob[]> {
  const res = await fetch(`${MCP_BASE}/meetings/jobs`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to list jobs"))
  return res.json()
}
