// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  fetchSetupHealth: vi.fn().mockResolvedValue({
    all_healthy: false,
    services: [{ name: "verification_pipeline", status: "error" }],
  }),
  MCP_BASE: "http://localhost:8888",
  mcpHeaders: vi.fn().mockReturnValue({}),
}))

import { HealthDashboard } from "@/components/setup/health-dashboard"

describe("HealthDashboard — verification pipeline row", () => {
  it("shows Re-check and hides the configure-a-provider copy when a provider is configured", async () => {
    render(<HealthDashboard polling={false} configuredProviders={["openrouter"]} />)

    expect(await screen.findByRole("button", { name: /re-check/i })).toBeInTheDocument()
    expect(screen.queryByText(/configure a provider first/i)).not.toBeInTheDocument()
  })

  it("shows the configure-a-provider copy and hides Re-check when no provider is configured", async () => {
    render(<HealthDashboard polling={false} configuredProviders={[]} />)

    expect(await screen.findByText(/configure a provider first/i)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /re-check/i })).not.toBeInTheDocument()
  })
})
