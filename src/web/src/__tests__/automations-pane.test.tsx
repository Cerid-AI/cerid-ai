// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  fetchAutomations: vi.fn(),
  createAutomation: vi.fn(),
  updateAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  toggleAutomation: vi.fn(),
  runAutomation: vi.fn(),
}))

import { fetchAutomations } from "@/lib/api"
import AutomationsPane from "@/components/automations/automations-pane"

const mockAutomations = [
  {
    id: "auto-1",
    name: "Daily Digest",
    description: "Summarizes daily activity",
    prompt: "Summarize today's activity across all domains",
    action: "digest" as const,
    schedule: "0 9 * * *",
    domains: ["coding"],
    enabled: true,
    run_count: 5,
    last_run_at: "2026-03-20T09:00:00Z",
    last_status: "success",
    created_at: "2026-03-01T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
  },
]

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("AutomationsPane", () => {
  it("renders New button", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue(mockAutomations)
    render(<AutomationsPane />)
    expect(await screen.findByText("New")).toBeInTheDocument()
  })

  it("renders automation cards after loading", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue(mockAutomations)
    render(<AutomationsPane />)
    expect(await screen.findByText("Daily Digest")).toBeInTheDocument()
  })

  it("shows empty state when no automations", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue([])
    render(<AutomationsPane />)
    await waitFor(() => {
      expect(screen.getByText("No automations yet")).toBeInTheDocument()
    })
  })
})
