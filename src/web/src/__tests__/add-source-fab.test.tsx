// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { AddSourceFab } from "@/components/sources/add-source-fab"

// Geometry the component's classes give it: a 56 px button (h-14 w-14) set
// 24 px in from the bottom-right corner (bottom-6 right-6), 44 px petals
// (h-11 w-11) centred on it and moved by --petal-x / --petal-y.
const CENTRE_TO_EDGE = 24 + 56 / 2
const PETAL = 44

function openPetals() {
  render(<AddSourceFab onSelectFamily={() => {}} />)
  fireEvent.click(screen.getByRole("button", { name: "Add a new source" }))
  return screen.getAllByRole("button", { name: /^Add .+ source$/ }).map((el) => ({
    name: el.getAttribute("aria-label"),
    x: parseFloat(el.style.getPropertyValue("--petal-x")),
    y: parseFloat(el.style.getPropertyValue("--petal-y")),
  }))
}

describe("AddSourceFab menu (audit 64)", () => {
  it("keeps every petal inside the viewport", () => {
    const petals = openPetals()
    expect(petals).toHaveLength(9)
    for (const p of petals) {
      expect(p.x + PETAL / 2, `${p.name} runs past the right edge`).toBeLessThanOrEqual(CENTRE_TO_EDGE)
      expect(p.y + PETAL / 2, `${p.name} runs past the bottom edge`).toBeLessThanOrEqual(CENTRE_TO_EDGE)
    }
  })

  it("does not overlap petals with each other or with the button", () => {
    const petals = openPetals()
    for (const [i, a] of petals.entries()) {
      expect(Math.hypot(a.x, a.y), `${a.name} overlaps the button`).toBeGreaterThanOrEqual(PETAL / 2 + 56 / 2)
      for (const b of petals.slice(i + 1)) {
        expect(Math.hypot(a.x - b.x, a.y - b.y), `${a.name} overlaps ${b.name}`).toBeGreaterThanOrEqual(PETAL)
      }
    }
  })
})
