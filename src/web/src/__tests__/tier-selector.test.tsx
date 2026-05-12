// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { Zap, FlaskConical } from "lucide-react"
import { TierSelector } from "@/components/ui/tier-selector"
import type { TierOption } from "@/components/ui/tier-selector"

const OPTIONS: TierOption[] = [
  {
    id: "quick",
    label: "Quick",
    Icon: Zap,
    description: "Fast responses with basic verification.",
  },
  {
    id: "balanced",
    label: "Balanced",
    Icon: FlaskConical,
    description: "Thorough retrieval with full verification pipeline.",
  },
  {
    id: "maximum",
    label: "Maximum",
    description: "All features enabled.",
    locked: true,
    lockedReason: "Pro",
  },
]

describe("TierSelector", () => {
  it("renders all option labels", () => {
    render(
      <TierSelector
        value="quick"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    expect(screen.getByText("Quick")).toBeInTheDocument()
    expect(screen.getByText("Balanced")).toBeInTheDocument()
    expect(screen.getByText("Maximum")).toBeInTheDocument()
  })

  it("renders descriptions", () => {
    render(
      <TierSelector
        value="quick"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    expect(screen.getByText("Fast responses with basic verification.")).toBeInTheDocument()
  })

  it("marks active option aria-checked=true", () => {
    render(
      <TierSelector
        value="balanced"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    expect(screen.getByRole("radio", { name: /balanced/i })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getByRole("radio", { name: /quick/i })).toHaveAttribute(
      "aria-checked",
      "false",
    )
  })

  it("calls onChange when an unlocked option is clicked", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <TierSelector
        value="quick"
        onChange={onChange}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    await user.click(screen.getByRole("radio", { name: /balanced/i }))
    expect(onChange).toHaveBeenCalledWith("balanced")
  })

  it("does not call onChange when a locked option is clicked", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <TierSelector
        value="quick"
        onChange={onChange}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    const lockedBtn = screen.getByRole("radio", { name: /maximum/i })
    expect(lockedBtn).toBeDisabled()
    await user.click(lockedBtn)
    expect(onChange).not.toHaveBeenCalled()
  })

  it("shows Pro badge on locked option", () => {
    render(
      <TierSelector
        value="quick"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    expect(screen.getByText("Pro")).toBeInTheDocument()
  })

  it("renders icon when provided", () => {
    render(
      <TierSelector
        value="quick"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    const quickBtn = screen.getByRole("radio", { name: /quick/i })
    expect(quickBtn.querySelector("svg")).toBeTruthy()
  })

  it("renders group with role=radiogroup and aria-label", () => {
    const { container } = render(
      <TierSelector
        value="quick"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    const group = container.querySelector('[role="radiogroup"]')
    expect(group).toHaveAttribute("aria-label", "Response tier")
  })

  it("uses custom activeClassName when provided", () => {
    render(
      <TierSelector
        value="quick"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
        activeClassName="border-primary bg-primary/5"
      />,
    )
    const quickBtn = screen.getByRole("radio", { name: /quick/i })
    expect(quickBtn.className).toContain("border-primary")
  })

  it("is axe-clean", async () => {
    const { container } = render(
      <TierSelector
        value="quick"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Response tier"
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
