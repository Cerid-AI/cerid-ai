// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { Zap } from "lucide-react"
import { SegmentedControl } from "@/components/ui/segmented-control"

const OPTIONS = [
  { value: "1h", label: "1h" },
  { value: "6h", label: "6h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
] as const

describe("SegmentedControl", () => {
  it("renders all options", () => {
    render(
      <SegmentedControl
        value="1h"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Time range"
      />,
    )
    expect(screen.getByText("1h")).toBeInTheDocument()
    expect(screen.getByText("6h")).toBeInTheDocument()
    expect(screen.getByText("24h")).toBeInTheDocument()
    expect(screen.getByText("7d")).toBeInTheDocument()
  })

  it("marks active option aria-checked=true, others false", () => {
    render(
      <SegmentedControl
        value="6h"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Time range"
      />,
    )
    expect(screen.getByText("6h").closest("button")).toHaveAttribute("aria-checked", "true")
    expect(screen.getByText("1h").closest("button")).toHaveAttribute("aria-checked", "false")
  })

  it("calls onChange when an option is clicked", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SegmentedControl
        value="1h"
        onChange={onChange}
        options={OPTIONS}
        ariaLabel="Time range"
      />,
    )
    await user.click(screen.getByText("24h"))
    expect(onChange).toHaveBeenCalledWith("24h")
  })

  it("navigates right with ArrowRight key", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SegmentedControl
        value="1h"
        onChange={onChange}
        options={OPTIONS}
        ariaLabel="Time range"
      />,
    )
    const firstButton = screen.getByText("1h").closest("button")!
    firstButton.focus()
    await user.keyboard("{ArrowRight}")
    expect(onChange).toHaveBeenCalledWith("6h")
  })

  it("wraps from last to first option on ArrowRight", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SegmentedControl
        value="7d"
        onChange={onChange}
        options={OPTIONS}
        ariaLabel="Time range"
      />,
    )
    screen.getByText("7d").closest("button")!.focus()
    await user.keyboard("{ArrowRight}")
    expect(onChange).toHaveBeenCalledWith("1h")
  })

  it("renders icon when provided", () => {
    render(
      <SegmentedControl
        value="1h"
        onChange={vi.fn()}
        options={[{ value: "1h", label: "1h", icon: Zap }]}
        ariaLabel="Time range"
      />,
    )
    const button = screen.getByText("1h").closest("button")!
    expect(button.querySelector("svg")).toBeTruthy()
  })

  it("renders the outer group with role=radiogroup and aria-label", () => {
    const { container } = render(
      <SegmentedControl
        value="1h"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Time range"
      />,
    )
    const group = container.querySelector('[role="radiogroup"]')
    expect(group).toHaveAttribute("aria-label", "Time range")
  })

  it("is axe-clean", async () => {
    const { container } = render(
      <SegmentedControl
        value="6h"
        onChange={vi.fn()}
        options={OPTIONS}
        ariaLabel="Time range"
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
