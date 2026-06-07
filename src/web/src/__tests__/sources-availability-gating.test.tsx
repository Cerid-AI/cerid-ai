// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Source-kind availability gating: unimplemented kinds (coming_soon) and
// OAuth-only kinds must be non-selectable in the add-source gallery, so the
// wizard can't drive POST /sources into a 501.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { SourcesEmptyGallery } from "@/components/sources/sources-empty-gallery"
import type { SourceKindMeta } from "@/lib/api/sources"

const KINDS: SourceKindMeta[] = [
  { kind: "rss", family: "feeds", tier: "core", availability: "available" },
  { kind: "folder", family: "files", tier: "core", availability: "coming_soon" },
  { kind: "gmail", family: "mail", tier: "pro", availability: "oauth" },
]

vi.mock("@/lib/api/sources", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/sources")>()),
  listSourceKinds: vi.fn(async () => KINDS),
}))

function renderGallery() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SourcesEmptyGallery onSelectKind={() => {}} />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe("SourcesEmptyGallery — availability gating", () => {
  it("enables available kinds and disables coming_soon / oauth", async () => {
    renderGallery()
    const available = await screen.findByRole("button", { name: /add rss/i })
    expect(available).toBeEnabled()

    const comingSoon = screen.getByRole("button", { name: /coming soon/i })
    expect(comingSoon).toBeDisabled()

    const oauth = screen.getByRole("button", { name: /connect in settings/i })
    expect(oauth).toBeDisabled()
  })

  it("shows Soon and Settings badges on gated kinds", async () => {
    renderGallery()
    await waitFor(() => expect(screen.getByText("Soon")).toBeInTheDocument())
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })
})
