// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// ConfidenceBandBadge is now a thin deprecated wrapper around TrustBandBadge.
// These tests verify the mapping contract (band → trust label) and
// accessibility requirements are still met through the wrapper.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { axe } from "jest-axe"
import { ConfidenceBandBadge } from "@/components/wiki/confidence-band-badge"
import type { ConfidenceBand } from "@/lib/types/wiki"

// Stub MutationObserver (TrustBandBadge registers a theme watcher).
const mockObserve = vi.fn()
const mockDisconnect = vi.fn()
vi.stubGlobal("MutationObserver", class {
  observe = mockObserve
  disconnect = mockDisconnect
})

beforeEach(() => {
  mockObserve.mockReset()
  mockDisconnect.mockReset()
})

// ---------------------------------------------------------------------------
// Band → trust label mapping
// ---------------------------------------------------------------------------

const BAND_LABEL_MAP: { band: ConfidenceBand; expectedText: string }[] = [
  { band: "high", expectedText: "verified" },
  { band: "medium", expectedText: "partial" },
  { band: "low", expectedText: "unverified" },
  { band: "unknown", expectedText: "unknown" },
]

describe("ConfidenceBandBadge — rendering (via TrustBandBadge)", () => {
  for (const { band, expectedText } of BAND_LABEL_MAP) {
    it(`maps "${band}" band to "${expectedText}" trust label`, () => {
      render(<ConfidenceBandBadge band={band} />)
      expect(screen.getByText(expectedText)).toBeTruthy()
    })

    it(`renders "${band}" band with trust aria-label`, () => {
      const { container } = render(<ConfidenceBandBadge band={band} />)
      const el = container.querySelector(`[aria-label*="Trust: ${expectedText}"]`)
      expect(el).not.toBeNull()
    })

    it(`renders icon for "${band}" band (aria-hidden)`, () => {
      const { container } = render(<ConfidenceBandBadge band={band} />)
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
    expect(screen.getByText("verified")).toBeTruthy()
    expect(container.querySelector("svg")).not.toBeNull()
  })

  it("low band has both icon and text", () => {
    const { container } = render(<ConfidenceBandBadge band="low" />)
    expect(screen.getByText("unverified")).toBeTruthy()
    expect(container.querySelector("svg")).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// axe-clean — all four bands
// ---------------------------------------------------------------------------

describe("ConfidenceBandBadge — axe-clean", () => {
  for (const { band } of BAND_LABEL_MAP) {
    it(`is axe-clean for "${band}" band`, async () => {
      const { container } = render(<ConfidenceBandBadge band={band} />)
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  }
})
