// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// SourcesActivityStream state-matrix tests.
//
// Two load-bearing cases:
//
// 1. The cold-mount failure: before WB-15 the pane rendered the new-user
//    "No activity yet" onboarding card during a backend outage, because
//    neither query destructured isError. Error and empty must stay
//    distinguishable.
//
// 2. F359 — the pane read /admin/ingest-history, a Redis stream whose only
//    writer, record_ingest_event(), has no callers anywhere in src/mcp. The
//    endpoint answered {"items":[],"total":0} on a deployment holding 1,006
//    artifacts with 183 ingested in the previous 24 hours, so the ledger was
//    permanently blank and a successful ingest was indistinguishable from a
//    failed one. The audit log behind /ingest_log is the ledger the ingestion
//    service actually appends to.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

vi.mock("@/lib/api/kb", () => ({
  fetchIngestionProgress: vi.fn(),
  fetchIngestLog: vi.fn(),
}))
vi.mock("@/lib/api/settings", () => ({
  fetchIngestHistory: vi.fn(),
}))

import { fetchIngestionProgress, fetchIngestLog } from "@/lib/api/kb"
import { fetchIngestHistory } from "@/lib/api/settings"
import { SourcesActivityStream } from "@/components/sources/activity-stream"

const mockProgress = fetchIngestionProgress as ReturnType<typeof vi.fn>
const mockLog = fetchIngestLog as ReturnType<typeof vi.fn>
const mockHistory = fetchIngestHistory as ReturnType<typeof vi.fn>

function renderStream() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<SourcesActivityStream />, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    ),
  })
}

const logEntry = (over: Record<string, unknown> = {}) => ({
  event: "ingest",
  artifact_id: "a1b2c3d4",
  domain: "projects",
  filename: "quarterly-notes.md",
  timestamp: new Date().toISOString(),
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  mockProgress.mockResolvedValue({ files: [], total_files: 0, completed_files: 0 })
  mockLog.mockResolvedValue([])
})

describe("SourcesActivityStream — state matrix", () => {
  it("error: a cold-mount fetch failure renders the error state, NOT the onboarding card (WB-15)", async () => {
    mockLog.mockRejectedValue(new Error("backend down"))

    renderStream()

    expect(await screen.findByText(/Couldn't load activity/i)).toBeInTheDocument()
    expect(screen.queryByText(/No activity yet/i)).not.toBeInTheDocument()

    // Retry re-invokes the failed query.
    mockLog.mockClear()
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    await waitFor(() => expect(mockLog).toHaveBeenCalled())
  })

  it("empty: genuinely empty responses render the onboarding card", async () => {
    renderStream()

    expect(await screen.findByText(/No activity yet/i)).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't load activity/i)).not.toBeInTheDocument()
  })

  it("success: settled history entries render in the Recent section", async () => {
    mockLog.mockResolvedValue([logEntry({ chunks: 4 })])

    renderStream()

    expect(await screen.findByText("Recent")).toBeInTheDocument()
    expect(screen.getByText("quarterly-notes.md")).toBeInTheDocument()
    expect(screen.getByText(/4 chunks/)).toBeInTheDocument()
    expect(screen.queryByText(/No activity yet/i)).not.toBeInTheDocument()
  })
})

describe("SourcesActivityStream — ingestion ledger (F359)", () => {
  it("reads the ledger the ingestion path actually writes to", async () => {
    mockLog.mockResolvedValue([logEntry()])

    renderStream()

    await waitFor(() => expect(mockLog).toHaveBeenCalled())
    expect(mockHistory).not.toHaveBeenCalled()
  })

  it("accepts the bare list /ingest_log returns on the wire", async () => {
    mockLog.mockResolvedValue([logEntry({ filename: "bare-list.md" })])

    renderStream()

    expect(await screen.findByText("bare-list.md")).toBeInTheDocument()
  })

  it("accepts a wrapped {entries} payload too", async () => {
    mockLog.mockResolvedValue({ total: 1, entries: [logEntry({ filename: "wrapped.md" })] })

    renderStream()

    expect(await screen.findByText("wrapped.md")).toBeInTheDocument()
  })

  it("keeps unrelated audit events out of the ingestion ledger", async () => {
    mockLog.mockResolvedValue([
      logEntry({ event: "query", filename: "", artifact_id: "q1" }),
      logEntry({ event: "scheduled_job", filename: "nightly", artifact_id: "s1" }),
      logEntry({ filename: "kept.md" }),
    ])

    renderStream()

    expect(await screen.findByText("kept.md")).toBeInTheDocument()
    expect(screen.queryByText("nightly")).not.toBeInTheDocument()
  })

  it("shows a duplicate as skipped rather than as a fresh ingest", async () => {
    mockLog.mockResolvedValue([logEntry({ event: "duplicate", filename: "already-here.md" })])

    renderStream()

    expect(await screen.findByText("already-here.md")).toBeInTheDocument()
    expect(screen.getByText(/skipped/i)).toBeInTheDocument()
  })

  it("falls back to the artifact id when the event carries no filename", async () => {
    mockLog.mockResolvedValue([logEntry({ filename: "" })])

    renderStream()

    expect(await screen.findByText("a1b2c3d4")).toBeInTheDocument()
  })
})
