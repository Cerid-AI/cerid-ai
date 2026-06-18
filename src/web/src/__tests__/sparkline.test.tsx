// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render } from "@testing-library/react"
import { Sparkline } from "@/components/ui/sparkline"

describe("Sparkline", () => {
  it("renders a flat baseline when fewer than 2 values", () => {
    const { container } = render(<Sparkline values={[5]} />)
    const svg = container.querySelector("svg")
    expect(svg).toBeTruthy()
    expect(svg?.querySelector("line")).toBeTruthy()
    expect(svg?.querySelector("path")).toBeNull()
  })

  it("builds a path with M + L commands matching the value count", () => {
    const { container } = render(<Sparkline values={[1, 2, 3, 4, 5]} />)
    const path = container.querySelector("path")?.getAttribute("d") ?? ""
    // 5 points = 1 M + 4 L commands
    expect((path.match(/M/g) ?? []).length).toBe(1)
    expect((path.match(/L/g) ?? []).length).toBe(4)
  })

  it("places the endpoint dot at the right edge when endpointDot is enabled", () => {
    const { container } = render(<Sparkline values={[1, 2, 3]} width={60} />)
    const circle = container.querySelector("circle")
    expect(circle).toBeTruthy()
    const cx = Number(circle?.getAttribute("cx"))
    // With padding=1.5 and width=60, the rightmost x is 58.5
    expect(cx).toBeGreaterThan(50)
  })

  it("omits the endpoint dot when endpointDot=false", () => {
    const { container } = render(
      <Sparkline values={[1, 2, 3]} endpointDot={false} />,
    )
    expect(container.querySelector("circle")).toBeNull()
  })

  it("respects an explicit yMin / yMax override", () => {
    // Same values, two ranges — the paths should differ.
    const { container: a } = render(<Sparkline values={[1, 2, 3]} />)
    const { container: b } = render(
      <Sparkline values={[1, 2, 3]} yMin={0} yMax={100} />,
    )
    const pathA = a.querySelector("path")?.getAttribute("d")
    const pathB = b.querySelector("path")?.getAttribute("d")
    expect(pathA).not.toBe(pathB)
  })

  it("attaches the cerid-sparkline-pulse class for the d-attr transition", () => {
    const { container } = render(<Sparkline values={[1, 2, 3]} />)
    const svg = container.querySelector("svg")
    expect(svg?.classList.contains("cerid-sparkline-pulse")).toBe(true)
  })
})
