// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import PrivacyCategory from "@/components/settings/categories/privacy"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"

const mockSettings: ServerSettings = {
  feature_tier: "community",
  feature_flags: {},
  categorize_mode: "smart",
  chunk_max_tokens: 512,
  chunk_overlap: 64,
  cost_sensitivity: "medium",
  enable_encryption: false,
  enable_feedback_loop: true,
  enable_hallucination_check: true,
  enable_memory_extraction: true,
  enable_model_router: false,
  hallucination_threshold: 0.7,
  enable_auto_inject: true,
  auto_inject_threshold: 0.55,
  domains: [],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "",
  machine_id: "test",
  version: "1.0.0",
  sensitive_domain_retrieval: false,
}

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const mockPatch = vi.fn().mockResolvedValue({ ok: true })
const mockRefresh = vi.fn()

const defaultProps: SettingsCategoryPageProps = {
  settings: mockSettings,
  patch: mockPatch,
  onRefresh: mockRefresh,
}

function ok(data: unknown) {
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

function mockApis() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
    if (url.includes("/settings/egress")) return ok({ egress: [] })
    if (url.includes("/settings")) return ok({
      private_mode_level: 0,
      enable_encryption: false,
    })
    return ok({})
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  mockPatch.mockClear()
})

describe("PrivacyCategory — rendering", () => {
  it("renders Private Mode, Retrieval Privacy, Data Egress and Data Protection sections", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    expect(screen.getAllByText("Private Mode").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("Retrieval Privacy")).toBeInTheDocument()
    expect(screen.getByText("Data Egress")).toBeInTheDocument()
    expect(screen.getAllByText("Data Protection").length).toBeGreaterThanOrEqual(1)
  })

  it("renders all 5 level buttons (L0–L4)", () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    expect(screen.getByRole("button", { name: /L0.*Off/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L1.*Skip saves/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L2.*skip KB injection/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L3.*no logging/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L4.*ephemeral/i })).toBeInTheDocument()
  })

  it("L0 is active by default (aria-pressed)", () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    const l0Btn = screen.getByRole("button", { name: /L0.*Off/i })
    expect(l0Btn).toHaveAttribute("aria-pressed", "true")
  })

  it("L4 button wraps in AlertDialog trigger", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    const l4Btn = screen.getByRole("button", { name: /L4.*ephemeral/i })
    await user.click(l4Btn)
    expect(await screen.findByText(/Enable L4.*ephemeral/i)).toBeInTheDocument()
  })

  it("L1 click updates active state without dialog", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    const l1Btn = screen.getByRole("button", { name: /L1.*Skip saves/i })
    await user.click(l1Btn)
    await waitFor(() => {
      expect(l1Btn).toHaveAttribute("aria-pressed", "true")
    })
  })

  it("shows alert banner when L4 is active", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    const l4Btn = screen.getByRole("button", { name: /L4.*ephemeral/i })
    await user.click(l4Btn)
    const enableBtn = await screen.findByRole("button", { name: /Enable L4/i })
    await user.click(enableBtn)
    expect(await screen.findByText(/L4 is active/i)).toBeInTheDocument()
  })
})

describe("PrivacyCategory — Retrieval Privacy toggle (Task 1.3c)", () => {
  it("renders the sensitive-domain switch off when settings.sensitive_domain_retrieval is false", () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    const toggle = screen.getByRole("switch", { name: /Include private domains \(iMessage\) in answers/i })
    expect(toggle).toHaveAttribute("aria-checked", "false")
  })

  it("toggling calls patch with sensitive_domain_retrieval: true", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<PrivacyCategory {...defaultProps} />, { wrapper })
    const toggle = screen.getByRole("switch", { name: /Include private domains \(iMessage\) in answers/i })
    await user.click(toggle)
    expect(mockPatch).toHaveBeenCalledWith({ sensitive_domain_retrieval: true })
  })

  it("renders the switch on when settings.sensitive_domain_retrieval is true", () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory {...defaultProps} settings={{ ...mockSettings, sensitive_domain_retrieval: true }} />, { wrapper })
    const toggle = screen.getByRole("switch", { name: /Include private domains \(iMessage\) in answers/i })
    expect(toggle).toHaveAttribute("aria-checked", "true")
  })
})

describe("PrivacyCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<PrivacyCategory {...defaultProps} />, { wrapper })
    expect(await axe(container)).toHaveNoViolations()
  })
})
