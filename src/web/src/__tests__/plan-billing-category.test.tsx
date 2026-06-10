// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// plan-billing.tsx ships as a static stub in the public/community edition
// (the live checkout/license surface is internal-only). This test covers the
// stub's render states; the full behavioural test is internal-only.

import { describe, it, expect, vi } from "vitest"
import { render as rtlRender, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import PlanBillingCategory from "@/components/settings/categories/plan-billing"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"

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
  version: "0.8.0",
}

function props(tier: string): SettingsCategoryPageProps {
  return {
    settings: { ...mockSettings, feature_tier: tier },
    patch: vi.fn().mockResolvedValue({ ok: true }),
    onRefresh: vi.fn(),
  }
}

describe("PlanBillingCategory (stub)", () => {
  it("shows the Community badge and upgrade blurb on the community tier", () => {
    render(<PlanBillingCategory {...props("community")} />)
    expect(screen.getByText("Community")).toBeInTheDocument()
    expect(screen.getByText(/Upgrading later takes one license key/i)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /cerid\.ai/i })).toBeInTheDocument()
  })

  it("reflects the active state on the pro tier", () => {
    render(<PlanBillingCategory {...props("pro")} />)
    expect(screen.getByText("Pro")).toBeInTheDocument()
    expect(screen.getByText(/Pro tier is active/i)).toBeInTheDocument()
  })

  it("treats enterprise as an active tier", () => {
    render(<PlanBillingCategory {...props("enterprise")} />)
    expect(screen.getByText(/Enterprise tier is active/i)).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const { container } = render(<PlanBillingCategory {...props("community")} />)
    expect(await axe(container)).toHaveNoViolations()
  })
})
