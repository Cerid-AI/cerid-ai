// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TypeScript types for the background-job processor subsystem (Phase P.2).
 *
 * Field names mirror the backend's ``JobRecord.to_dict()`` output exactly.
 * ``queue_sizes`` uses priority name strings as keys (matching the backend's
 * ``{p.value: count}`` serialisation from ``Priority`` enum values).
 */

// ---------------------------------------------------------------------------
// Enums / unions
// ---------------------------------------------------------------------------

export type ProcessorPriority = "high" | "medium" | "low"

export type JobState = "pending" | "running" | "completed" | "failed" | "paused"

// ---------------------------------------------------------------------------
// Response shapes
// ---------------------------------------------------------------------------

/**
 * GET /processor/status
 *
 * ``queue_sizes`` keys are priority name strings (e.g. "high", "medium",
 * "low").  The backend builds this from ``{p.value: count for p, count in
 * sizes_by_priority.items()}``.
 */
export interface ProcessorStatus {
  queue_sizes: Record<ProcessorPriority | string, number>
  paused: boolean
  jobs_completed_24h: number
  cost_usd_7d: number
  throttled_ticks_1h: number
}

/**
 * GET /processor/recent?limit=N — single job record.
 *
 * Maps ``JobRecord.to_dict()`` field for field.  Optional fields (``started_at``,
 * ``completed_at``, token actuals, model, error_message) are ``null`` when not
 * yet set.
 */
export interface JobRecord {
  id: string
  job_type: string
  state: JobState
  priority: ProcessorPriority | string
  payload: Record<string, unknown>
  enqueued_at: string          // ISO-8601
  retry_count: number
  started_at: string | null    // ISO-8601
  completed_at: string | null  // ISO-8601
  estimated_tokens_in: number
  estimated_tokens_out: number
  actual_tokens_in: number | null
  actual_tokens_out: number | null
  requires_llm: boolean
  model: string | null
  error_message: string | null
}

/**
 * POST /processor/pause  →  { paused: true }
 * POST /processor/resume →  { paused: false }
 */
export interface ProcessorPauseResponse {
  paused: boolean
}
