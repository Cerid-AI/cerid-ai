// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { TrustBandBadge, type TrustState } from "@/components/ui/trust-band-badge"

// MutationObserver is available in jsdom but we stub it to avoid noise
// from the theme-watcher useEffect.
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

const STATES: { trust: TrustState; expectedText: string }[] = [
  { trust: "verified", expectedText: "verified" },
  { trust: "partial", expectedText: "partial" },
  { trust: "unverified", expectedText: "unverified" },
  { trust: "unknown", expectedText: "unknown" },
]

// ---------------------------------------------------------------------------
// All four trust states render
// ---------------------------------------------------------------------------

describe("TrustBandBadge — rendering", () => {
  for (const { trust, expectedText } of STATES) {
    it(`renders "${trust}" state with correct label`, () => {
      render(<TrustBandBadge trust={trust} />)
      expect(screen.getByText(expectedText)).toBeTruthy()
    })

    it(`renders "${trust}" state with aria-label`, () => {
      const { container } = render(<TrustBandBadge trust={trust} />)
      const el = container.querySelector(`[aria-label*="Trust: ${expectedText}"]`)
      expect(el).not.toBeNull()
    })

    it(`renders icon for "${trust}" (aria-hidden)`, () => {
      const { container } = render(<TrustBandBadge trust={trust} />)
      const svg = container.querySelector('svg[aria-hidden="true"]')
      expect(svg).not.toBeNull()
    })
  }
})

// ---------------------------------------------------------------------------
// Without evidence counts: no Popover trigger (just a plain span badge)
// ---------------------------------------------------------------------------

describe("TrustBandBadge — without evidence data", () => {
  it("renders plain span (not button) when no counts supplied", () => {
    render(<TrustBandBadge trust="verified" />)
    // No popover = the badge renders as a <span>, not a <button>.
    const el = screen.getByText("verified").closest("[aria-label]")
    expect(el?.tagName.toLowerCase()).toBe("span")
  })
})

// ---------------------------------------------------------------------------
// Evidence popover opens on click
// ---------------------------------------------------------------------------

describe("TrustBandBadge — evidence popover", () => {
  it("shows corroborating count in popover", async () => {
    const user = userEvent.setup()
    render(<TrustBandBadge trust="verified" corroboratingCount={5} />)
    // When counts are provided the trigger is a <button>
    const trigger = screen.getByRole("button")
    await user.click(trigger)
    expect(screen.getByText(/5/)).toBeTruthy()
    expect(screen.getByText(/corroborating/i)).toBeTruthy()
  })

  it("shows contradiction count in popover", async () => {
    const user = userEvent.setup()
    render(<TrustBandBadge trust="partial" contradictionCount={2} />)
    const trigger = screen.getByRole("button")
    await user.click(trigger)
    expect(screen.getByText(/2/)).toBeTruthy()
    expect(screen.getByText(/contradiction/i)).toBeTruthy()
  })

  it("shows both counts when both are provided", async () => {
    const user = userEvent.setup()
    render(
      <TrustBandBadge trust="unverified" corroboratingCount={3} contradictionCount={1} />,
    )
    const trigger = screen.getByRole("button")
    await user.click(trigger)
    expect(screen.getByText(/3/)).toBeTruthy()
    expect(screen.getByText(/1/)).toBeTruthy()
  })

  it("uses singular 'source' for count of 1", async () => {
    const user = userEvent.setup()
    render(<TrustBandBadge trust="verified" corroboratingCount={1} />)
    const trigger = screen.getByRole("button")
    await user.click(trigger)
    // "1 corroborating source" (not "sources")
    expect(screen.getByText(/corroborating source$/)).toBeTruthy()
  })

  it("uses singular 'contradiction' for count of 1", async () => {
    const user = userEvent.setup()
    render(<TrustBandBadge trust="partial" contradictionCount={1} />)
    const trigger = screen.getByRole("button")
    await user.click(trigger)
    expect(screen.getByText(/contradiction$/)).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Color-independent: icon + text both present (WCAG: not color alone)
// ---------------------------------------------------------------------------

describe("TrustBandBadge — a11y: color not sole indicator", () => {
  for (const { trust } of STATES) {
    it(`"${trust}" has both icon and text`, () => {
      const { container } = render(<TrustBandBadge trust={trust} />)
      expect(container.querySelector("svg")).not.toBeNull()
      expect(container.textContent).toContain(trust)
    })
  }
})

// ---------------------------------------------------------------------------
// axe-clean — all four states
// ---------------------------------------------------------------------------

describe("TrustBandBadge — axe-clean", () => {
  for (const { trust } of STATES) {
    it(`is axe-clean for "${trust}"`, async () => {
      const { container } = render(<TrustBandBadge trust={trust} />)
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  }

  it("is axe-clean with evidence popover trigger (button)", async () => {
    const { container } = render(
      <TrustBandBadge trust="verified" corroboratingCount={3} contradictionCount={0} />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
