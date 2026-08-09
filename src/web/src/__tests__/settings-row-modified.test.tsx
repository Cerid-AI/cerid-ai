// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * SettingRow modified-state affordance (ST2) — a changed-from-default control
 * is visibly flagged and offers a one-click reset.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SettingRow } from "@/components/settings/settings-primitives"
import { ModifiedSettingsProvider } from "@/components/settings/modified-context"
import { getDef } from "@/lib/settings-registry"

const def = getDef("retrieval.contextInjection.threshold")!

function renderRow(value: { ids: Set<string>; reset?: () => void }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ModifiedSettingsProvider value={value}>
        <SettingRow def={def}>
          <input aria-label="control" />
        </SettingRow>
      </ModifiedSettingsProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

describe("SettingRow modified affordance", () => {
  it("flags a row whose id is in the modified set", () => {
    renderRow({ ids: new Set([def.id]) })
    expect(screen.getByText("Modified")).toBeInTheDocument()
  })

  it("does not flag a row that is at its default", () => {
    renderRow({ ids: new Set() })
    expect(screen.queryByText("Modified")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /reset/i })).not.toBeInTheDocument()
  })

  it("offers reset-to-default that calls the provider reset with the def", async () => {
    const reset = vi.fn()
    renderRow({ ids: new Set([def.id]), reset })
    await userEvent.click(screen.getByRole("button", { name: /reset .* to default/i }))
    expect(reset).toHaveBeenCalledWith(def)
  })
})
