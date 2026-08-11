// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// The add-source wizard is reachable from the AddSourceFab (⌘⇧S) without ever
// passing through SourcesConnectors or the empty gallery, both of which do gate
// Pro kinds. Until 2026-08-10 it had no entitlement logic at all: `selectable`
// keyed off availability and `tier === "pro"` only prefixed the label, so a
// community user could pick a Pro kind, fill the form, press Connect, and meet
// the backend's 403 as a raw error string at the end of the flow.
//
// The server is still the enforcement point (POST /sources 403s for Pro kinds
// at community tier). These cases are about not presenting a dead end.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

import type { SourceKindMeta } from "@/lib/api/sources"
import type { SourceKindMetaExt } from "@/components/sources/source-kind-meta"

const KINDS: SourceKindMetaExt[] = [
  { kind: "rss", family: "feeds", tier: "core", availability: "available" },
  { kind: "gmail", family: "mail", tier: "pro", availability: "available" },
]

vi.mock("@/lib/api/sources", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/sources")>()),
  listSourceKinds: vi.fn(async () => KINDS as SourceKindMeta[]),
  createSource: vi.fn(),
}))

vi.mock("@/lib/api/billing", () => ({
  fetchCapabilities: vi.fn(),
}))

import { createSource } from "@/lib/api/sources"
import { fetchCapabilities } from "@/lib/api/billing"
import { SourceAddWizard } from "@/components/sources/source-add-wizard"

const mockCreate = createSource as ReturnType<typeof vi.fn>
const mockCapabilities = fetchCapabilities as ReturnType<typeof vi.fn>

/** Realistic capabilities payload — `features` carries the per-flag detail the
    hook resolves. /sources/kinds ships no feature_flag, so the wizard's own
    verdict comes from the registry-tier fallback; the map is populated anyway
    so the fixture matches what a real server sends. */
function capabilities(tier: "community" | "pro" | "enterprise") {
  return {
    tier,
    features: {
      gmail_connector: { enabled: tier !== "community", tier_required: "pro" },
      ocr_parsing: { enabled: true, tier_required: "community" },
    },
    buckets: {},
  }
}

function renderWizard(props: { initialKind?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <SourceAddWizard open onClose={() => {}} {...props} />,
    {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      ),
    },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("SourceAddWizard — Pro gate", () => {
  it("routes a Pro kind to the upgrade dialog instead of the configure step", async () => {
    const user = userEvent.setup()
    mockCapabilities.mockResolvedValue(capabilities("community"))
    renderWizard()

    // The tile stays visible and clickable — it is the funnel — but says why.
    const tile = await screen.findByRole("button", { name: /gmail — requires cerid pro/i })
    expect(tile).toBeEnabled()
    await user.click(tile)

    expect(await screen.findByText(/Gmail requires Cerid Pro/i)).toBeInTheDocument()
    // The configure step must not have been reached.
    expect(screen.queryByLabelText(/display name/i)).not.toBeInTheDocument()
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it("lets an entitled tier through to the configure step", async () => {
    const user = userEvent.setup()
    mockCapabilities.mockResolvedValue(capabilities("pro"))
    renderWizard()

    const tile = await screen.findByRole("button", { name: /add gmail/i })
    await user.click(tile)

    expect(await screen.findByLabelText(/display name/i)).toBeInTheDocument()
    expect(screen.queryByText(/requires Cerid Pro/i)).not.toBeInTheDocument()
  })

  it("never locks a Core kind", async () => {
    const user = userEvent.setup()
    mockCapabilities.mockResolvedValue(capabilities("community"))
    renderWizard()

    await user.click(await screen.findByRole("button", { name: /add rss/i }))
    expect(await screen.findByLabelText(/display name/i)).toBeInTheDocument()
  })

  it("gates a pre-selected Pro kind, which skips the tile grid entirely", async () => {
    // initialKind deep-links straight to the configure step (the FAB and the
    // gallery hand-off both use it), so the tile-click check alone would leave
    // the one entry point that renders no tile ungated.
    mockCapabilities.mockResolvedValue(capabilities("community"))
    renderWizard({ initialKind: "gmail" })

    expect(await screen.findByText(/Gmail is part of Cerid Pro/i)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^connect$/i })).not.toBeInTheDocument()
  })

  it("fails CLOSED when capabilities cannot be loaded", async () => {
    // forFlag's second argument is the registry-tier fallback: without it an
    // unresolvable entitlement returns AVAILABLE, so the gate would disappear
    // exactly when the server can't be asked.
    mockCapabilities.mockRejectedValue(new Error("network down"))
    renderWizard({ initialKind: "gmail" })

    expect(await screen.findByText(/Gmail is part of Cerid Pro/i)).toBeInTheDocument()
  })
})
