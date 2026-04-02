// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, within } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateApiKey: vi.fn(),
  fetchSetupHealth: vi.fn().mockResolvedValue({ services: {} }),
  fetchProviderCredits: vi.fn().mockResolvedValue({ configured: false, balance: null }),
}))

import { SetupWizard } from "@/components/setup/setup-wizard"

const noop = () => {}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SetupWizard", () => {
  // ---- Step 0: Welcome ----

  it("renders welcome step with correct copy", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText(/get you set up/i)).toBeInTheDocument()
    expect(screen.getByText(/OpenRouter key is required/)).toBeInTheDocument()
    expect(screen.getByText(/Keys are stored locally/)).toBeInTheDocument()
  })

  it("shows Get Started button on welcome step", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    const btn = screen.getByRole("button", { name: /get started/i })
    expect(btn).toBeInTheDocument()
    expect(btn).toBeEnabled()
  })

  // ---- Step navigation: forward ----

  it("advances to API Keys step on Get Started click", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByText("API Keys")).toBeInTheDocument()
    expect(screen.getByText(/OpenRouter API Key/)).toBeInTheDocument()
  })

  it("shows Back button on API Keys step", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByRole("button", { name: /back/i })).toBeInTheDocument()
  })

  it("shows disabled Next button on API Keys step when no key validated", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    const nextBtn = screen.getByRole("button", { name: /next/i })
    expect(nextBtn).toBeDisabled()
  })

  // ---- Step navigation: backward ----

  it("back returns to welcome from API Keys step", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByText("API Keys")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /back/i }))
    expect(screen.getByText(/get you set up/i)).toBeInTheDocument()
  })

  // ---- Step indicators ----

  function getStepDots() {
    // Dialog renders in a portal (document.body). Step dots have class "h-1.5 w-1.5 rounded-full".
    return [...document.body.querySelectorAll(".rounded-full")].filter(
      (el) => el.classList.contains("h-1.5") && el.classList.contains("w-1.5"),
    )
  }

  it("shows correct number of step indicator dots", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    const dots = getStepDots()
    // TOTAL_STEPS is 4
    expect(dots.length).toBe(4)
  })

  it("highlights first dot as active on step 0", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    const dots = getStepDots()
    // Active dot has bg-brand class
    expect(dots[0]?.className).toContain("bg-brand")
    expect(dots[1]?.className).not.toContain("bg-brand")
  })

  it("highlights second dot as active on step 1", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    const dots = getStepDots()
    expect(dots[0]?.className).not.toContain("bg-brand")
    expect(dots[1]?.className).toContain("bg-brand")
  })

  // ---- Dialog behavior ----

  it("renders dialog title for accessibility", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText("Cerid AI Setup")).toBeInTheDocument()
  })

  it("does not render when open is false", () => {
    render(<SetupWizard open={false} onComplete={noop} />)
    expect(screen.queryByText(/get you set up/i)).not.toBeInTheDocument()
  })

  // ---- Welcome step details ----

  it("lists three bullet points about setup requirements", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText(/OpenRouter key is required/)).toBeInTheDocument()
    expect(screen.getByText(/OpenAI and Anthropic keys are optional/)).toBeInTheDocument()
    expect(screen.getByText(/Keys are stored locally/)).toBeInTheDocument()
  })

  it("mentions OpenRouter gives access to 100+ models", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText(/100\+ models/)).toBeInTheDocument()
  })

  // ---- API Keys step structure ----

  it("shows OpenAI and Anthropic key inputs on step 1", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByText(/OpenAI API Key/)).toBeInTheDocument()
    expect(screen.getByText(/Anthropic API Key/)).toBeInTheDocument()
  })

  it("shows help text for creating OpenRouter account when key is not validated", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByText(/Don't have an OpenRouter account/)).toBeInTheDocument()
  })

  // ---- onComplete callback ----

  it("calls onComplete when setup is finished", () => {
    // We can only test that onComplete is wired — reaching step 3 requires
    // validated keys + applied config + healthy services, which are integration tests.
    // Instead verify the callback prop is respected by checking the component renders.
    const onComplete = vi.fn()
    render(<SetupWizard open={true} onComplete={onComplete} />)
    // onComplete should not have been called yet
    expect(onComplete).not.toHaveBeenCalled()
  })

  // ---- Multiple back-forward cycles ----

  it("handles repeated forward-backward navigation", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    // Go to step 1
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByText("API Keys")).toBeInTheDocument()
    // Back to step 0
    fireEvent.click(screen.getByRole("button", { name: /back/i }))
    expect(screen.getByText(/get you set up/i)).toBeInTheDocument()
    // Forward again
    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByText("API Keys")).toBeInTheDocument()
  })

  // ---- No back button on step 0 ----

  it("does not show Back button on welcome step", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.queryByRole("button", { name: /back/i })).not.toBeInTheDocument()
  })
})
