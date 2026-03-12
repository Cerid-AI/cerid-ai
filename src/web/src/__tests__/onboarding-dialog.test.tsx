// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { OnboardingDialog } from "@/components/onboarding/onboarding-dialog"
import { UIModeProvider } from "@/contexts/ui-mode-context"

function Wrapper({ children }: { children: React.ReactNode }) {
  return <UIModeProvider>{children}</UIModeProvider>
}

beforeEach(() => {
  localStorage.clear()
})

// Helper: the step title is the visible h3, not the sr-only DialogTitle
function getStepTitle(name: string) {
  return screen.getByRole("heading", { level: 3, name })
}

describe("OnboardingDialog", () => {
  it("renders welcome step initially", () => {
    render(<OnboardingDialog open onComplete={() => {}} />, { wrapper: Wrapper })
    expect(getStepTitle("Welcome to Cerid AI")).toBeInTheDocument()
  })

  it("advances through steps with Next button", () => {
    render(<OnboardingDialog open onComplete={() => {}} />, { wrapper: Wrapper })
    expect(getStepTitle("Welcome to Cerid AI")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Next"))
    expect(getStepTitle("Navigate with the Sidebar")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Next"))
    expect(getStepTitle("Chat & Knowledge")).toBeInTheDocument()
  })

  it("shows mode selection on the last step", () => {
    render(<OnboardingDialog open onComplete={() => {}} />, { wrapper: Wrapper })

    // Advance through all content steps
    fireEvent.click(screen.getByText("Next"))
    fireEvent.click(screen.getByText("Next"))
    fireEvent.click(screen.getByText("Next"))

    expect(getStepTitle("Choose Your Mode")).toBeInTheDocument()
    expect(screen.getByText("☕ Simple")).toBeInTheDocument()
    expect(screen.getByText("🔧 Advanced")).toBeInTheDocument()
  })

  it("calls onComplete and sets localStorage on finish", () => {
    const onComplete = vi.fn()
    render(<OnboardingDialog open onComplete={onComplete} />, { wrapper: Wrapper })

    // Advance to mode selection
    fireEvent.click(screen.getByText("Next"))
    fireEvent.click(screen.getByText("Next"))
    fireEvent.click(screen.getByText("Next"))

    // Click Get Started
    fireEvent.click(screen.getByText("Get Started"))
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem("cerid-onboarding-complete")).toBe("true")
  })

  it("Back button returns to previous step", () => {
    render(<OnboardingDialog open onComplete={() => {}} />, { wrapper: Wrapper })

    fireEvent.click(screen.getByText("Next"))
    expect(getStepTitle("Navigate with the Sidebar")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Back"))
    expect(getStepTitle("Welcome to Cerid AI")).toBeInTheDocument()
  })

  it("does not show Back button on first step", () => {
    render(<OnboardingDialog open onComplete={() => {}} />, { wrapper: Wrapper })
    expect(screen.queryByText("Back")).not.toBeInTheDocument()
  })
})
