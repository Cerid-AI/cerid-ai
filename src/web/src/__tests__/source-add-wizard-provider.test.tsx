// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Webhook-backed kinds (chat_capture / dev_events) require a provider whose
// recipe normalizes the inbound payload. The wizard must render a provider
// picker and gate Connect until one is chosen — otherwise POST /sources 422s.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { SourceAddWizard } from "@/components/sources/source-add-wizard"
import type { SourceKindMeta } from "@/lib/api/sources"

const KINDS: SourceKindMeta[] = [
  {
    kind: "chat_capture",
    family: "webhook",
    tier: "core",
    availability: "available",
    providers: ["discord", "matrix", "slack", "teams"],
  },
]

vi.mock("@/lib/api/sources", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/sources")>()),
  listSourceKinds: vi.fn(async () => KINDS),
  createSource: vi.fn(),
}))

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SourceAddWizard open initialKind="chat_capture" onClose={() => {}} />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe("SourceAddWizard — provider picker", () => {
  it("renders the recipe providers as options for a webhook-backed kind", async () => {
    renderWizard()
    expect(await screen.findByLabelText(/provider/i)).toBeInTheDocument()
    for (const p of ["Slack", "Discord", "Teams", "Matrix"]) {
      expect(screen.getByRole("option", { name: p })).toBeInTheDocument()
    }
  })

  it("gates Connect until a provider is picked, then enables it", async () => {
    renderWizard()
    const select = await screen.findByLabelText(/provider/i)
    const connect = screen.getByRole("button", { name: /^connect$/i })
    expect(connect).toBeDisabled()

    fireEvent.change(select, { target: { value: "slack" } })
    expect(connect).toBeEnabled()
  })
})
