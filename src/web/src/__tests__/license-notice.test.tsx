// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// The friction is deliberately targeted. These tests pin WHO sees a notice as
// firmly as what it says: a Core user who has never trialed must never be
// nagged (they are the top of the funnel), and a paying customer must never be
// called unlicensed.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render as rtlRender, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import { LicenseNotice, LicenseStatusBadge } from "@/components/settings/license-notice"
import * as billingApi from "@/lib/api/billing"

vi.mock("@/lib/api/billing")

function render(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

function withState(license_state: string, tier = "pro") {
  vi.mocked(billingApi.fetchCapabilities).mockResolvedValue({
    tier, features: {}, buckets: {}, license_state,
  } as never)
}

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
})

describe("LicenseNotice", () => {
  it("calls out an unlicensed Pro install", async () => {
    withState("unlicensed_pro")
    render(<LicenseNotice />)
    expect(await screen.findByTestId("unlicensed-pro-notice")).toBeInTheDocument()
    expect(screen.getByText(/Unlicensed copy of Cerid Pro/i)).toBeInTheDocument()
  })

  it("gives the unlicensed notice both a buy path and a trial path", async () => {
    withState("unlicensed_pro")
    render(<LicenseNotice />)
    await screen.findByTestId("unlicensed-pro-notice")
    expect(screen.getByRole("link", { name: /get a license/i }))
      .toHaveAttribute("href", "https://cerid.ai/pricing")
    expect(screen.getByRole("button", { name: /start the free trial/i })).toBeInTheDocument()
  })

  it("does not let the unlicensed notice be dismissed", async () => {
    withState("unlicensed_pro")
    render(<LicenseNotice />)
    await screen.findByTestId("unlicensed-pro-notice")
    expect(screen.queryByRole("button", { name: /dismiss/i })).not.toBeInTheDocument()
  })

  it("says nothing to a licensed customer", async () => {
    withState("licensed")
    const { container } = render(<LicenseNotice />)
    await vi.waitFor(() => expect(billingApi.fetchCapabilities).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("says nothing during an active trial", async () => {
    withState("trial")
    const { container } = render(<LicenseNotice />)
    await vi.waitFor(() => expect(billingApi.fetchCapabilities).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("says nothing to a Core user who has never trialed", async () => {
    // The funnel's top. Nagging here costs adoption and buys nothing.
    withState("community", "community")
    const { container } = render(<LicenseNotice />)
    await vi.waitFor(() => expect(billingApi.fetchCapabilities).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("reminds once the trial has lapsed, and snoozes for a week", async () => {
    withState("trial_expired", "community")
    const user = userEvent.setup()
    render(<LicenseNotice />)

    await screen.findByTestId("trial-expired-notice")
    await user.click(screen.getByRole("button", { name: /dismiss for a week/i }))

    expect(screen.queryByTestId("trial-expired-notice")).not.toBeInTheDocument()
    const until = Number(localStorage.getItem("cerid.trialExpiredSnoozedUntil"))
    expect(until).toBeGreaterThan(Date.now())
  })

  it("stays hidden while the snooze is live", async () => {
    localStorage.setItem("cerid.trialExpiredSnoozedUntil", String(Date.now() + 86400_000))
    withState("trial_expired", "community")
    render(<LicenseNotice />)
    await vi.waitFor(() => expect(billingApi.fetchCapabilities).toHaveBeenCalled())
    expect(screen.queryByTestId("trial-expired-notice")).not.toBeInTheDocument()
  })

  it("returns after the snooze lapses", async () => {
    localStorage.setItem("cerid.trialExpiredSnoozedUntil", String(Date.now() - 1000))
    withState("trial_expired", "community")
    render(<LicenseNotice />)
    expect(await screen.findByTestId("trial-expired-notice")).toBeInTheDocument()
  })

  it("treats a corrupt snooze value as no snooze", async () => {
    localStorage.setItem("cerid.trialExpiredSnoozedUntil", "not-a-number")
    withState("trial_expired", "community")
    render(<LicenseNotice />)
    expect(await screen.findByTestId("trial-expired-notice")).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    withState("unlicensed_pro")
    const { container } = render(<LicenseNotice />)
    await screen.findByTestId("unlicensed-pro-notice")
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("LicenseStatusBadge", () => {
  it("marks an unlicensed install from any screen", async () => {
    withState("unlicensed_pro")
    render(<LicenseStatusBadge />)
    expect(await screen.findByText(/unlicensed/i)).toBeInTheDocument()
  })

  it("is absent for every other state", async () => {
    for (const state of ["licensed", "trial", "trial_expired", "community"]) {
      withState(state)
      const { container, unmount } = render(<LicenseStatusBadge />)
      await vi.waitFor(() => expect(billingApi.fetchCapabilities).toHaveBeenCalled())
      expect(container, `badge should be hidden for ${state}`).toBeEmptyDOMElement()
      unmount()
    }
  })
})
