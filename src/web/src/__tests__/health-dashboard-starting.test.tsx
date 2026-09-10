// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  fetchSetupHealth: vi.fn().mockResolvedValue({
    all_healthy: false,
    services: [{ name: "mcp", status: "starting" }],
  }),
  MCP_BASE: "http://localhost:8888",
  mcpHeaders: vi.fn().mockReturnValue({}),
}))

import { HealthDashboard } from "@/components/setup/health-dashboard"

describe("HealthDashboard — starting status", () => {
  it("shows Starting… copy for a starting service instead of Offline", async () => {
    render(<HealthDashboard polling={false} />)

    await waitFor(() => {
      expect(screen.getByText("Starting…")).toBeInTheDocument()
    })
    expect(screen.queryByText("Offline")).not.toBeInTheDocument()
  })
})
