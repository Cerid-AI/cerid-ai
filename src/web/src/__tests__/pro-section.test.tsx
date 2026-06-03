// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// pro-section.tsx ships as a static stub in the public/community edition (the
// live capabilities-driven Pro pane is internal-only). This test covers the
// stub's render states; the full behavioural test is internal-only.

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { axe } from "jest-axe"
import { ProSection } from "@/components/settings/pro-section"

describe("ProSection (stub)", () => {
  it("shows the Community badge and upgrade blurb on the community tier", () => {
    render(<ProSection featureTier="community" />)
    expect(screen.getByText("Pro Features")).toBeInTheDocument()
    expect(screen.getByText("Community")).toBeInTheDocument()
    expect(screen.getByText(/Pro tier unlocks advanced features/i)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /cerid\.ai\/pro/i })).toBeInTheDocument()
  })

  it("reflects the active state on the pro tier", () => {
    render(<ProSection featureTier="pro" />)
    expect(screen.getByText("Active")).toBeInTheDocument()
    expect(screen.getByText(/Pro tier is active/i)).toBeInTheDocument()
  })

  it("treats enterprise as an active tier", () => {
    render(<ProSection featureTier="enterprise" />)
    expect(screen.getByText("Active")).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const { container } = render(<ProSection featureTier="community" />)
    expect(await axe(container)).toHaveNoViolations()
  })
})
