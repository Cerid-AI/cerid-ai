// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Community-edition Plan & Billing. Checkout is hosted on cerid.ai, but
// activation and the trial are local, so this covers the conversion path a
// self-hosted user actually walks. The commercial (Stripe) variant of this
// pane has its own internal test.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render as rtlRender, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import PlanBillingCategory from "@/components/settings/categories/plan-billing"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"
import * as licenseApi from "@/lib/api/license"

vi.mock("@/lib/api/license")

function render(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return rtlRender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

const mockSettings: ServerSettings = {
  feature_tier: "community",
  feature_flags: {},
  categorize_mode: "smart",
  chunk_max_tokens: 400,
  chunk_overlap: 0.2,
  cost_sensitivity: "medium",
  enable_encryption: false,
  enable_feedback_loop: false,
  enable_hallucination_check: true,
  enable_memory_extraction: false,
  enable_model_router: false,
  hallucination_threshold: 0.75,
  enable_auto_inject: false,
  auto_inject_threshold: 0.82,
  domains: [],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "local",
  machine_id: "test-machine",
  version: "1.0.1",
}

function props(tier: string): SettingsCategoryPageProps {
  return {
    settings: { ...mockSettings, feature_tier: tier },
    patch: vi.fn().mockResolvedValue({ ok: true }),
    onRefresh: vi.fn(),
  }
}

function status(over: Partial<licenseApi.LicenseStatus> = {}): licenseApi.LicenseStatus {
  return {
    tier: "community",
    active: false,
    source: "default",
    key_masked: null,
    expires_at: null,
    trial: { available: true, active: false, days_remaining: null, expires_at: null },
    purchase_url: "https://cerid.ai/pricing",
    ...over,
  }
}

beforeEach(() => {
  vi.mocked(licenseApi.fetchLicenseStatus).mockResolvedValue(status())
  vi.mocked(licenseApi.activateLicense).mockResolvedValue(status({ tier: "pro", active: true }))
  vi.mocked(licenseApi.startTrial).mockResolvedValue(status({ tier: "pro", active: true }))
})

describe("PlanBillingCategory (community edition)", () => {
  it("links to the pricing page, not the /pro path that 404'd", async () => {
    render(<PlanBillingCategory {...props("community")} />)
    const link = await screen.findByRole("link", { name: /see plans/i })
    expect(link).toHaveAttribute("href", "https://cerid.ai/pricing")
  })

  it("offers a license field so a purchased key can actually be activated", async () => {
    render(<PlanBillingCategory {...props("community")} />)
    expect(await screen.findByLabelText("License key")).toBeInTheDocument()
  })

  it("activates a pasted key", async () => {
    const user = userEvent.setup()
    render(<PlanBillingCategory {...props("community")} />)

    await user.type(await screen.findByLabelText("License key"), "CERID-PRO-ABCD-EFGH")
    await user.click(screen.getByRole("button", { name: /activate/i }))

    await waitFor(() =>
      expect(licenseApi.activateLicense).toHaveBeenCalledWith("CERID-PRO-ABCD-EFGH"),
    )
    expect(await screen.findByText(/license activated/i)).toBeInTheDocument()
  })

  it("surfaces the server's reason when activation is rejected", async () => {
    vi.mocked(licenseApi.activateLicense).mockRejectedValue(new Error("Invalid key format"))
    const user = userEvent.setup()
    render(<PlanBillingCategory {...props("community")} />)

    await user.type(await screen.findByLabelText("License key"), "nope")
    await user.click(screen.getByRole("button", { name: /activate/i }))

    expect(await screen.findByText(/invalid key format/i)).toBeInTheDocument()
  })

  it("cannot submit an empty key", async () => {
    render(<PlanBillingCategory {...props("community")} />)
    expect(await screen.findByRole("button", { name: /activate/i })).toBeDisabled()
  })

  it("offers the no-card trial while it is still available", async () => {
    const user = userEvent.setup()
    render(<PlanBillingCategory {...props("community")} />)

    await user.click(await screen.findByRole("button", { name: /start 14-day free trial/i }))

    await waitFor(() => expect(licenseApi.startTrial).toHaveBeenCalled())
  })

  it("hides the trial button once the trial has been used", async () => {
    vi.mocked(licenseApi.fetchLicenseStatus).mockResolvedValue(
      status({ trial: { available: false, active: false, days_remaining: null, expires_at: null } }),
    )
    render(<PlanBillingCategory {...props("community")} />)
    await screen.findByLabelText("License key")
    expect(screen.queryByRole("button", { name: /free trial/i })).not.toBeInTheDocument()
  })

  it("shows days remaining while a trial is running", async () => {
    vi.mocked(licenseApi.fetchLicenseStatus).mockResolvedValue(
      status({
        tier: "pro", active: true, source: "trial",
        trial: { available: false, active: true, days_remaining: 9, expires_at: 1 },
      }),
    )
    render(<PlanBillingCategory {...props("community")} />)
    expect(await screen.findByText(/9 days left/i)).toBeInTheDocument()
  })

  it("reports the paid tier as active", async () => {
    vi.mocked(licenseApi.fetchLicenseStatus).mockResolvedValue(
      status({ tier: "pro", active: true, source: "license_key", key_masked: "CERID-PRO-****-WXYZ" }),
    )
    render(<PlanBillingCategory {...props("pro")} />)
    expect(await screen.findByText("CERID-PRO-****-WXYZ")).toBeInTheDocument()
    expect(screen.getByText(/Pro is active/i)).toBeInTheDocument()
  })

  it("does not misreport a paid customer as Community when the fetch fails", async () => {
    // Regression guard for the error-as-empty class: falling back to a bare
    // default would tell a Pro customer they are on the free tier.
    vi.mocked(licenseApi.fetchLicenseStatus).mockRejectedValue(new Error("network down"))
    render(<PlanBillingCategory {...props("pro")} />)
    expect(await screen.findByText("Pro")).toBeInTheDocument()
    expect(screen.getByText(/couldn't reach the licensing endpoint/i)).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const { container } = render(<PlanBillingCategory {...props("community")} />)
    await screen.findByLabelText("License key")
    expect(await axe(container)).toHaveNoViolations()
  })
})
