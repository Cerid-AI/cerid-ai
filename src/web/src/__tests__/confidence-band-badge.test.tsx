// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { axe } from "jest-axe"
import { ConfidenceBandBadge } from "@/components/wiki/confidence-band-badge"
import type { ConfidenceBand } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const BANDS: { band: ConfidenceBand; expectedText: string }[] = [
  { band: "high", expectedText: "high" },
  { band: "medium", expectedText: "medium" },
  { band: "low", expectedText: "low" },
  { band: "unknown", expectedText: "unknown" },
]

// ---------------------------------------------------------------------------
// All four bands render
// ---------------------------------------------------------------------------

describe("ConfidenceBandBadge — rendering", () => {
  for (const { band, expectedText } of BANDS) {
    it(`renders "${band}" band with correct text`, () => {
      render(<ConfidenceBandBadge band={band} />)
      expect(screen.getByText(expectedText)).toBeTruthy()
    })

    it(`renders "${band}" band with correct aria-label`, () => {
      const { container } = render(<ConfidenceBandBadge band={band} />)
      const el = container.querySelector(`[aria-label="Confidence: ${expectedText}"]`)
      expect(el).not.toBeNull()
    })

    it(`renders icon for "${band}" band (aria-hidden)`, () => {
      const { container } = render(<ConfidenceBandBadge band={band} />)
      // lucide icons render as SVGs with aria-hidden
      const svg = container.querySelector('svg[aria-hidden="true"]')
      expect(svg).not.toBeNull()
    })
  }
})

// ---------------------------------------------------------------------------
// Color-independent — icon + text both present (WCAG: not color alone)
// ---------------------------------------------------------------------------

describe("ConfidenceBandBadge — a11y: color not sole indicator", () => {
  it("high band has both icon and text", () => {
    const { container } = render(<ConfidenceBandBadge band="high" />)
    expect(screen.getByText("high")).toBeTruthy()
    expect(container.querySelector("svg")).not.toBeNull()
  })

  it("low band has both icon and text", () => {
    const { container } = render(<ConfidenceBandBadge band="low" />)
    expect(screen.getByText("low")).toBeTruthy()
    expect(container.querySelector("svg")).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// axe-clean — all four bands
// ---------------------------------------------------------------------------

describe("ConfidenceBandBadge — axe-clean", () => {
  for (const { band } of BANDS) {
    it(`is axe-clean for "${band}" band`, async () => {
      const { container } = render(<ConfidenceBandBadge band={band} />)
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  }
})

// ---------------------------------------------------------------------------
// Snapshots
// ---------------------------------------------------------------------------

describe("ConfidenceBandBadge — snapshots", () => {
  for (const { band } of BANDS) {
    it(`snapshot matches for "${band}" band`, () => {
      const { container } = render(<ConfidenceBandBadge band={band} />)
      expect(container).toMatchSnapshot()
    })
  }
})
