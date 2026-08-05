// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Processor API client (Phase P.2).
 *
 * Wraps the backend routes:
 *   GET  /processor/status
 *   GET  /processor/recent?limit=N
 *   POST /processor/pause
 *   POST /processor/resume
 */

import { MCP_BASE, mcpHeaders } from "./common"
import type {
  ProcessorStatus,
  JobRecord,
  ProcessorPauseResponse,
} from "@/lib/types/processor"

// ---------------------------------------------------------------------------
// GET /processor/status
// ---------------------------------------------------------------------------

export async function fetchProcessorStatus(): Promise<ProcessorStatus> {
  const res = await fetch(`${MCP_BASE}/processor/status`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(`Processor status fetch failed (${res.status})`)
  }
  return (await res.json()) as ProcessorStatus
}

// ---------------------------------------------------------------------------
// GET /processor/recent?limit=N
// ---------------------------------------------------------------------------

export async function fetchProcessorRecent(limit = 20): Promise<JobRecord[]> {
  const res = await fetch(
    `${MCP_BASE}/processor/recent?limit=${limit}`,
    { headers: mcpHeaders() },
  )
  if (!res.ok) {
    throw new Error(`Processor recent fetch failed (${res.status})`)
  }
  return (await res.json()) as JobRecord[]
}

// ---------------------------------------------------------------------------
// POST /processor/pause
// ---------------------------------------------------------------------------

export async function pauseProcessor(): Promise<ProcessorPauseResponse> {
  const res = await fetch(`${MCP_BASE}/processor/pause`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(`Processor pause failed (${res.status})`)
  }
  return (await res.json()) as ProcessorPauseResponse
}

// ---------------------------------------------------------------------------
// POST /processor/resume
// ---------------------------------------------------------------------------

export async function resumeProcessor(): Promise<ProcessorPauseResponse> {
  const res = await fetch(`${MCP_BASE}/processor/resume`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(`Processor resume failed (${res.status})`)
  }
  return (await res.json()) as ProcessorPauseResponse
}
