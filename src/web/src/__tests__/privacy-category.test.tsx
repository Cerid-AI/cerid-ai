// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import PrivacyCategory from "@/components/settings/categories/privacy"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
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
})

describe("PrivacyCategory — rendering", () => {
  it("renders Private Mode and Data Protection sections", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory />, { wrapper })
    expect(screen.getAllByText("Private Mode").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("Data Protection").length).toBeGreaterThanOrEqual(1)
  })

  it("renders all 5 level buttons (L0–L4)", () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory />, { wrapper })
    expect(screen.getByRole("button", { name: /L0.*Off/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L1.*Skip saves/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L2.*skip KB injection/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L3.*no logging/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /L4.*ephemeral/i })).toBeInTheDocument()
  })

  it("L0 is active by default (aria-pressed)", () => {
    vi.stubGlobal("fetch", mockApis())
    render(<PrivacyCategory />, { wrapper })
    const l0Btn = screen.getByRole("button", { name: /L0.*Off/i })
    expect(l0Btn).toHaveAttribute("aria-pressed", "true")
  })

  it("L4 button wraps in AlertDialog trigger", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<PrivacyCategory />, { wrapper })
    const l4Btn = screen.getByRole("button", { name: /L4.*ephemeral/i })
    await user.click(l4Btn)
    expect(await screen.findByText(/Enable L4.*ephemeral/i)).toBeInTheDocument()
  })

  it("L1 click updates active state without dialog", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<PrivacyCategory />, { wrapper })
    const l1Btn = screen.getByRole("button", { name: /L1.*Skip saves/i })
    await user.click(l1Btn)
    await waitFor(() => {
      expect(l1Btn).toHaveAttribute("aria-pressed", "true")
    })
  })

  it("shows alert banner when L4 is active", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<PrivacyCategory />, { wrapper })
    const l4Btn = screen.getByRole("button", { name: /L4.*ephemeral/i })
    await user.click(l4Btn)
    const enableBtn = await screen.findByRole("button", { name: /Enable L4/i })
    await user.click(enableBtn)
    expect(await screen.findByText(/L4 is active/i)).toBeInTheDocument()
  })
})

describe("PrivacyCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<PrivacyCategory />, { wrapper })
    expect(await axe(container)).toHaveNoViolations()
  })
})
