// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// SourcesActivityStream state-matrix tests. The load-bearing case is the
// cold-mount failure: before WB-15 the pane rendered the new-user
// "No activity yet" onboarding card during a backend outage, because neither
// query destructured isError (and fetchIngestHistory fabricated an empty page
// on HTTP error, WB-10). Error and empty must be distinguishable.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

vi.mock("@/lib/api/kb", () => ({
  fetchIngestionProgress: vi.fn(),
}))
vi.mock("@/lib/api/settings", () => ({
  fetchIngestHistory: vi.fn(),
}))

import { fetchIngestionProgress } from "@/lib/api/kb"
import { fetchIngestHistory } from "@/lib/api/settings"
import { SourcesActivityStream } from "@/components/sources/activity-stream"

const mockProgress = fetchIngestionProgress as ReturnType<typeof vi.fn>
const mockHistory = fetchIngestHistory as ReturnType<typeof vi.fn>

function renderStream() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<SourcesActivityStream />, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    ),
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockProgress.mockResolvedValue({ files: [], total_files: 0, completed_files: 0 })
  mockHistory.mockResolvedValue({ items: [], total: 0, next_cursor: null })
})

describe("SourcesActivityStream — state matrix", () => {
  it("error: a cold-mount fetch failure renders the error state, NOT the onboarding card (WB-15)", async () => {
    mockHistory.mockRejectedValue(new Error("backend down"))

    renderStream()

    expect(await screen.findByText(/Couldn't load activity/i)).toBeInTheDocument()
    expect(screen.queryByText(/No activity yet/i)).not.toBeInTheDocument()

    // Retry re-invokes the failed query.
    mockHistory.mockClear()
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    await waitFor(() => expect(mockHistory).toHaveBeenCalled())
  })

  it("empty: genuinely empty responses render the onboarding card", async () => {
    renderStream()

    expect(await screen.findByText(/No activity yet/i)).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't load activity/i)).not.toBeInTheDocument()
  })

  it("success: settled history entries render in the Recent section", async () => {
    mockHistory.mockResolvedValue({
      items: [
        {
          id: "h1",
          filename: "quarterly-notes.md",
          source_type: "upload",
          domain: "projects",
          status: "success",
          timestamp: new Date().toISOString(),
          chunks: 4,
          error: "",
        },
      ],
      total: 1,
      next_cursor: null,
    })

    renderStream()

    expect(await screen.findByText("Recent")).toBeInTheDocument()
    expect(screen.getByText("quarterly-notes.md")).toBeInTheDocument()
    expect(screen.queryByText(/No activity yet/i)).not.toBeInTheDocument()
  })
})
