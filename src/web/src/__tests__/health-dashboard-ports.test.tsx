// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  fetchSetupHealth: vi.fn().mockResolvedValue({
    all_healthy: true,
    services: [
      { name: "neo4j", status: "healthy", port: 7484 },
      { name: "chromadb", status: "healthy", port: 8011 },
      { name: "redis", status: "healthy", port: 6389 },
      { name: "mcp", status: "healthy", port: 8898 },
      { name: "verification_pipeline", status: "healthy", port: 0 },
    ],
  }),
  MCP_BASE: "http://localhost:8898",
  mcpHeaders: vi.fn().mockReturnValue({}),
}))

import { HealthDashboard } from "@/components/setup/health-dashboard"

describe("HealthDashboard — ports", () => {
  it("shows the ports the stack reports, not the defaults", async () => {
    render(<HealthDashboard polling={false} />)

    await waitFor(() => expect(screen.getByText(":8898")).toBeInTheDocument())
    for (const port of [":7484", ":8011", ":6389"]) {
      expect(screen.getByText(port)).toBeInTheDocument()
    }
    for (const port of [":7474", ":8001", ":6379", ":8888", ":0"]) {
      expect(screen.queryByText(port)).not.toBeInTheDocument()
    }
  })
})
