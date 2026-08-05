// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, act } from "@testing-library/react"
import { OpeningSequence } from "@/components/ui/opening-sequence"

describe("OpeningSequence", () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.useRealTimers()
  })

  it("renders the C-mark on first paint when no session flag set", () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    })
    const { container } = render(<OpeningSequence />)
    expect(container.querySelector("svg")).toBeInTheDocument()
  })

  it("renders nothing when sessionStorage flag already set", () => {
    sessionStorage.setItem("cerid:opening-sequence-played", "1")
    const { container } = render(<OpeningSequence />)
    expect(container.firstChild).toBeNull()
  })

  it("skips animation when prefers-reduced-motion is set", () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    })
    const { container } = render(<OpeningSequence />)
    expect(container.firstChild).toBeNull()
    expect(sessionStorage.getItem("cerid:opening-sequence-played")).toBe("1")
  })

  it("auto-dismisses after the animation window", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    })
    vi.useFakeTimers()
    const { container } = render(<OpeningSequence />)
    expect(container.querySelector("svg")).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(1400)
    })
    expect(container.firstChild).toBeNull()
    expect(sessionStorage.getItem("cerid:opening-sequence-played")).toBe("1")
  })
})

describe("LiquidGlassDefs", () => {
  it("mounts the SVG filter at id=cerid-liquid-glass", async () => {
    const { LiquidGlassDefs } = await import("@/components/ui/liquid-glass-defs")
    render(<LiquidGlassDefs />)
    // The filter is mounted by id inside a hidden SVG; the consumer
    // references it via `filter: url(#cerid-liquid-glass)`.
    const filter = document.querySelector("#cerid-liquid-glass")
    expect(filter).toBeInTheDocument()
    expect(filter?.querySelector("feDisplacementMap")).toBeInTheDocument()
  })
})
