// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Source-kind availability gating: unimplemented kinds (coming_soon),
// OAuth-only kinds, and desktop-helper-backed kinds whose helper is absent
// (requires_desktop) must be non-selectable in the add-source gallery, so
// the wizard can't drive POST /sources into a 501/422.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { SourcesEmptyGallery } from "@/components/sources/sources-empty-gallery"
import type { SourceKindMeta } from "@/lib/api/sources"
import type { SourceKindMetaExt } from "@/components/sources/source-kind-meta"

const KINDS: SourceKindMetaExt[] = [
  { kind: "rss", family: "feeds", tier: "core", availability: "available" },
  { kind: "folder", family: "files", tier: "core", availability: "coming_soon" },
  { kind: "gmail", family: "mail", tier: "pro", availability: "oauth" },
  {
    kind: "apple_mail",
    family: "mail",
    tier: "pro",
    availability: "requires_desktop",
    requires_desktop: true,
  },
]

vi.mock("@/lib/api/sources", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/sources")>()),
  listSourceKinds: vi.fn(async () => KINDS as SourceKindMeta[]),
}))

function renderGallery(
  onSelectKind: (kind: string) => void = () => {},
  onOpenConnector?: (kind: string) => void,
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SourcesEmptyGallery onSelectKind={onSelectKind} onOpenConnector={onOpenConnector} />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe("SourcesEmptyGallery — availability gating", () => {
  it("enables available kinds and disables coming_soon / oauth (no connector handler)", async () => {
    renderGallery()
    const available = await screen.findByRole("button", { name: /add rss/i })
    expect(available).toBeEnabled()

    const comingSoon = screen.getByRole("button", { name: /coming soon/i })
    expect(comingSoon).toBeDisabled()

    // Without an onOpenConnector wire-up the tile stays disabled, but the
    // copy names the actual destination (Sources → Connectors), not Settings.
    const oauth = screen.getByRole("button", { name: /gmail — set up in sources → connectors/i })
    expect(oauth).toBeDisabled()
  })

  it("shows Soon and Connector badges on gated kinds", async () => {
    renderGallery()
    await waitFor(() => expect(screen.getByText("Soon")).toBeInTheDocument())
    expect(screen.getByText("Connector")).toBeInTheDocument()
  })

  it("oauth tiles become actionable when onOpenConnector is provided", async () => {
    const user = userEvent.setup()
    const onSelectKind = vi.fn()
    const onOpenConnector = vi.fn()
    renderGallery(onSelectKind, onOpenConnector)
    const oauth = await screen.findByRole("button", { name: /connect gmail/i })
    expect(oauth).toBeEnabled()
    await user.click(oauth)
    expect(onOpenConnector).toHaveBeenCalledWith("gmail")
    // The oauth tile must never fall through to the add-source wizard.
    expect(onSelectKind).not.toHaveBeenCalled()
  })

  it("disables requires_desktop kinds with the desktop-app badge", async () => {
    renderGallery()
    const tile = await screen.findByRole("button", {
      name: /apple mail — requires the cerid desktop app/i,
    })
    expect(tile).toBeDisabled()
    // Badge text + accessible label
    expect(screen.getByText("Desktop app")).toBeInTheDocument()
    expect(
      screen.getByLabelText("Requires the Cerid desktop app"),
    ).toBeInTheDocument()
  })

  it("does not fire onSelectKind for a requires_desktop tile", async () => {
    const user = userEvent.setup()
    const onSelectKind = vi.fn()
    renderGallery(onSelectKind)
    const tile = await screen.findByRole("button", {
      name: /apple mail — requires the cerid desktop app/i,
    })
    await user.click(tile).catch(() => {}) // pointer-events blocked on disabled
    expect(onSelectKind).not.toHaveBeenCalled()
  })
})
