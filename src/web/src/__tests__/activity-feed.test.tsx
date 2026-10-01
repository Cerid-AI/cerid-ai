// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// The Knowledge pane's activity feed read /admin/ingest-history, a stream
// nothing wrote to, so it said "No ingestion activity yet." on every
// deployment. It reads the audit log behind /ingest_log.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api/kb", () => ({
  fetchIngestLog: vi.fn(),
}))

import { fetchIngestLog } from "@/lib/api/kb"
import { ActivityFeed } from "@/components/kb/ActivityFeed"

const mockLog = fetchIngestLog as ReturnType<typeof vi.fn>

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
})

describe("ActivityFeed", () => {
  it("lists what the ingest log holds", async () => {
    mockLog.mockResolvedValue([logEntry(), logEntry({ event: "duplicate", filename: "again.md" })])

    render(<ActivityFeed />)

    expect(await screen.findByText("quarterly-notes.md")).toBeInTheDocument()
    expect(screen.getByText("again.md")).toBeInTheDocument()
  })

  it("keeps events that are not ingests out of the feed", async () => {
    mockLog.mockResolvedValue([
      logEntry({ event: "scheduled_job", filename: "nightly" }),
      logEntry({ filename: "kept.md" }),
    ])

    render(<ActivityFeed />)

    expect(await screen.findByText("kept.md")).toBeInTheDocument()
    expect(screen.queryByText("nightly")).not.toBeInTheDocument()
  })

  it("says so when the log cannot be read", async () => {
    mockLog.mockRejectedValue(new Error("backend down"))

    render(<ActivityFeed />)

    expect(await screen.findByText(/Couldn't load ingestion activity/)).toBeInTheDocument()
    expect(screen.queryByText(/No ingestion activity yet/)).not.toBeInTheDocument()
  })
})
