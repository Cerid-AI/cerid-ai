// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { AdvancedMode } from "@/components/common/advanced-mode"
import { UIModeProvider } from "@/contexts/ui-mode-context"

beforeEach(() => {
  localStorage.clear()
})

describe("AdvancedMode", () => {
  it("renders children when mode is advanced", () => {
    localStorage.setItem("cerid-ui-mode", "advanced")
    render(
      <UIModeProvider>
        <AdvancedMode>
          <span>power panel</span>
        </AdvancedMode>
      </UIModeProvider>,
    )
    expect(screen.getByText("power panel")).toBeInTheDocument()
  })

  it("renders nothing when mode is simple (no fallback)", () => {
    localStorage.setItem("cerid-ui-mode", "simple")
    render(
      <UIModeProvider>
        <AdvancedMode>
          <span>power panel</span>
        </AdvancedMode>
      </UIModeProvider>,
    )
    expect(screen.queryByText("power panel")).not.toBeInTheDocument()
  })

  it("renders fallback when mode is simple", () => {
    localStorage.setItem("cerid-ui-mode", "simple")
    render(
      <UIModeProvider>
        <AdvancedMode fallback={<span>enable advanced to see this</span>}>
          <span>power panel</span>
        </AdvancedMode>
      </UIModeProvider>,
    )
    expect(screen.queryByText("power panel")).not.toBeInTheDocument()
    expect(screen.getByText("enable advanced to see this")).toBeInTheDocument()
  })

  it("renders nothing for new users in simple mode (default state)", () => {
    // No localStorage set → new user → simple mode by default
    render(
      <UIModeProvider>
        <AdvancedMode>
          <span>advanced content</span>
        </AdvancedMode>
      </UIModeProvider>,
    )
    expect(screen.queryByText("advanced content")).not.toBeInTheDocument()
  })

  it("renders children for returning users (onboarding complete → advanced default)", () => {
    localStorage.setItem("cerid-onboarding-complete", "true")
    render(
      <UIModeProvider>
        <AdvancedMode>
          <span>advanced content</span>
        </AdvancedMode>
      </UIModeProvider>,
    )
    expect(screen.getByText("advanced content")).toBeInTheDocument()
  })
})
