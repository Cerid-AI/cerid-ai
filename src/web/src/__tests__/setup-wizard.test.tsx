// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateApiKey: vi.fn(),
  fetchSetupHealth: vi.fn().mockResolvedValue({ services: {} }),
}))

import { SetupWizard } from "@/components/setup/setup-wizard"

const noop = () => {}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SetupWizard", () => {
  it("renders step 0 welcome content", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText(/get you set up/i)).toBeInTheDocument()
  })

  it("renders Get Started button on step 0", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText("Get Started")).toBeInTheDocument()
  })

  it("mentions OpenRouter key requirement", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText(/OpenRouter key is required/)).toBeInTheDocument()
  })
})
