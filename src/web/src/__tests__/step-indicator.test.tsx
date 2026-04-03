// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { StepIndicator } from "@/components/setup/step-indicator"
import type { StepDef } from "@/components/setup/step-indicator"

const STEPS: StepDef[] = [
  { label: "Welcome", shortLabel: "Welcome" },
  { label: "System Check", shortLabel: "Check" },
  { label: "API Keys", shortLabel: "Keys" },
  { label: "Knowledge Base", shortLabel: "KB" },
  { label: "Ollama", shortLabel: "Ollama" },
]

describe("StepIndicator", () => {
  it("renders the correct number of steps", () => {
    render(
      <StepIndicator steps={STEPS} currentStep={0} skippedSteps={new Set()} />,
    )
    // Each step renders its shortLabel as a <span>
    for (const step of STEPS) {
      expect(screen.getByText(step.shortLabel)).toBeInTheDocument()
    }
  })

  it("shows active state for the current step", () => {
    const { container } = render(
      <StepIndicator steps={STEPS} currentStep={2} skippedSteps={new Set()} />,
    )
    // The active step gets the "text-brand" class
    const labels = container.querySelectorAll("span")
    const activeLabel = Array.from(labels).find((el) => el.textContent === "Keys")
    expect(activeLabel).toBeTruthy()
    // The parent div of the active label should contain brand color styling
    const parentDiv = activeLabel!.closest("div.flex.items-center.gap-1.rounded-full")
    expect(parentDiv?.className).toContain("text-brand")
    expect(parentDiv?.className).toContain("bg-brand/10")
  })

  it("shows completed state for previous steps", () => {
    const { container } = render(
      <StepIndicator steps={STEPS} currentStep={3} skippedSteps={new Set()} />,
    )
    // Steps 0, 1, 2 should be completed and have green text
    const stepDivs = container.querySelectorAll("div.flex.items-center.gap-1.rounded-full")
    // Steps 0, 1, 2 are completed => text-green-600
    for (let i = 0; i < 3; i++) {
      expect(stepDivs[i]?.className).toContain("text-green-600")
    }
  })

  it("shows skipped state for skipped steps", () => {
    const { container } = render(
      <StepIndicator
        steps={STEPS}
        currentStep={4}
        skippedSteps={new Set([1, 3])}
      />,
    )
    const stepDivs = container.querySelectorAll("div.flex.items-center.gap-1.rounded-full")
    // Step 1 and step 3 should have skipped styling (text-muted-foreground/50)
    expect(stepDivs[1]?.className).toContain("text-muted-foreground/50")
    expect(stepDivs[3]?.className).toContain("text-muted-foreground/50")
    // Step 0 and step 2 should be completed (not skipped)
    expect(stepDivs[0]?.className).toContain("text-green-600")
    expect(stepDivs[2]?.className).toContain("text-green-600")
  })
})
