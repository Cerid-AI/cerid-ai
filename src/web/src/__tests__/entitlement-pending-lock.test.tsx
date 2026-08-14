// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * sf5-02 tail — a lock verdict is never asserted before capabilities resolve.
 *
 * `useEntitlements` falls back to the registry tier while `GET
 * /billing/capabilities` is in flight, so a Pro-entitled row resolves to
 * "locked" for everyone — including licensed users. The row stays inert, but
 * it must say it is still checking rather than claim the plan is insufficient.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SettingRow } from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"

const fetchCapabilities = vi.hoisted(() => vi.fn())

vi.mock("@/lib/api/billing", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/billing")>()),
  fetchCapabilities,
}))

const proDef = getDef("retrieval.smartRag.weights")!

function renderRow() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingRow def={proDef}>
        <input aria-label="control" />
      </SettingRow>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  fetchCapabilities.mockReset()
})

describe("pending entitlement lock", () => {
  it("says it is checking while capabilities are in flight", async () => {
    fetchCapabilities.mockReturnValue(new Promise(() => {})) // never resolves
    renderRow()

    expect(await screen.findByText("Checking plan")).toBeInTheDocument()
    expect(screen.queryByText("Pro")).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /plan status loading/i }),
    ).toBeInTheDocument()
  })

  it("asserts the lock once capabilities say the tier is insufficient", async () => {
    fetchCapabilities.mockResolvedValue({
      tier: "community",
      features: {
        custom_smart_rag: { enabled: false, tier_required: "pro" },
      },
    })
    renderRow()

    expect(
      await screen.findByRole("button", { name: /requires the pro plan/i }),
    ).toBeInTheDocument()
    expect(screen.queryByText("Checking plan")).not.toBeInTheDocument()
  })

  it("shows no lock at all once capabilities say the feature is available", async () => {
    fetchCapabilities.mockResolvedValue({
      tier: "pro",
      features: {
        custom_smart_rag: { enabled: true, tier_required: "pro" },
      },
    })
    renderRow()

    expect(await screen.findByLabelText("control")).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByText("Checking plan")).not.toBeInTheDocument(),
    )
    expect(screen.queryByText("Pro")).not.toBeInTheDocument()
  })
})
