// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  fetchSetupHealth: vi.fn().mockResolvedValue({
    all_healthy: true,
    services: [
      { name: "mcp", status: "setup_mode", port: 8888 },
      { name: "redis", status: "healthy", port: 6379 },
    ],
  }),
  MCP_BASE: "http://localhost:8888",
  mcpHeaders: vi.fn().mockReturnValue({}),
}))

import { HealthDashboard } from "@/components/setup/health-dashboard"

describe("HealthDashboard — API in setup mode", () => {
  it("shows the API as running, waiting for setup, not as offline", async () => {
    render(<HealthDashboard polling={false} />)

    await waitFor(() => {
      expect(screen.getByText("Waiting for setup")).toBeInTheDocument()
    })
    expect(screen.queryByText("Offline")).not.toBeInTheDocument()
    expect(screen.queryByText("Check Docker Desktop is running")).not.toBeInTheDocument()
    expect(screen.getByText("All required services are healthy")).toBeInTheDocument()
  })
})
