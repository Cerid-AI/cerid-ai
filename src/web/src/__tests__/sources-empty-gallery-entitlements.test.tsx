// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// The empty gallery used to gate Pro tiles per TIER (`tier === "community"`),
// so every Pro tile locked or unlocked together and a flag the server had
// granted below Pro still routed to the upgrade dialog. /sources/kinds now
// carries `feature_flag` per kind, and the gallery resolves each tile's lock
// through useEntitlements().forFlag — "locked" is the only state an upgrade
// fixes. These cases pin the per-flag resolution and the loading/error
// treatment (verdict suppressed in flight, fail-closed on a failed fetch).

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

import type { SourceKindMeta } from "@/lib/api/sources"
import type { SourceKindMetaExt } from "@/components/sources/source-kind-meta"

const KINDS: SourceKindMetaExt[] = [
  { kind: "rss", family: "feeds", tier: "core", availability: "available" },
  {
    kind: "apple_mail",
    family: "mail",
    tier: "pro",
    availability: "available",
    feature_flag: "apple_mail_reader",
  },
  {
    kind: "meeting_audio",
    family: "files",
    tier: "pro",
    availability: "available",
    feature_flag: "meeting_diarization",
  },
]

vi.mock("@/lib/api/sources", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/sources")>()),
  listSourceKinds: vi.fn(async () => KINDS as SourceKindMeta[]),
}))

vi.mock("@/lib/api/billing", () => ({
  fetchCapabilities: vi.fn(),
}))

import { fetchCapabilities } from "@/lib/api/billing"
import { SourcesEmptyGallery } from "@/components/sources/sources-empty-gallery"

const mockCapabilities = fetchCapabilities as ReturnType<typeof vi.fn>

function renderGallery(handlers: {
  onSelectKind?: (kind: string) => void
  onProLocked?: (kind: string) => void
} = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <SourcesEmptyGallery
      onSelectKind={handlers.onSelectKind ?? (() => {})}
      onProLocked={handlers.onProLocked}
    />,
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

describe("SourcesEmptyGallery — per-flag Pro gate", () => {
  it("routes a locked kind to the upgrade path", async () => {
    const user = userEvent.setup()
    mockCapabilities.mockResolvedValue({
      tier: "community",
      features: {
        apple_mail_reader: { enabled: false, tier_required: "pro" },
        meeting_diarization: { enabled: false, tier_required: "pro" },
      },
      buckets: {},
    })
    const onSelectKind = vi.fn()
    const onProLocked = vi.fn()
    renderGallery({ onSelectKind, onProLocked })

    await user.click(await screen.findByRole("button", { name: /add apple mail \(pro\)/i }))
    expect(onProLocked).toHaveBeenCalledWith("apple_mail")
    expect(onSelectKind).not.toHaveBeenCalled()
  })

  it("resolves per FLAG, not per tier: a granted flag opens normally while a locked sibling stays gated", async () => {
    // The distinguishing case for the old `tier === "community"` gate: the
    // server has granted meeting_diarization below Pro, so that tile must NOT
    // route to the upgrade dialog even though the account tier is community.
    const user = userEvent.setup()
    mockCapabilities.mockResolvedValue({
      tier: "community",
      features: {
        apple_mail_reader: { enabled: false, tier_required: "pro" },
        meeting_diarization: { enabled: true, tier_required: "community" },
      },
      buckets: {},
    })
    const onSelectKind = vi.fn()
    const onProLocked = vi.fn()
    renderGallery({ onSelectKind, onProLocked })

    await user.click(await screen.findByRole("button", { name: /add meeting audio \(pro\)/i }))
    expect(onSelectKind).toHaveBeenCalledWith("meeting_audio")
    expect(onProLocked).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: /add apple mail \(pro\)/i }))
    expect(onProLocked).toHaveBeenCalledWith("apple_mail")
  })

  it("suppresses every verdict while capabilities load", async () => {
    // tier defaults to "community" in flight — rendering tiles then would
    // paint a paying customer the upgrade interception on first paint.
    mockCapabilities.mockReturnValue(new Promise(() => {}))
    renderGallery({ onProLocked: vi.fn() })

    expect(await screen.findByText(/loading sources/i)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /add apple mail/i })).not.toBeInTheDocument()
  })

  it("fails CLOSED when capabilities cannot be loaded, without blocking the gallery", async () => {
    // A failed fetch is not a settled community verdict, but the gate must
    // not vanish exactly when the server can't be asked — Pro kinds stay
    // locked (registry fallback) and the tiles still render.
    const user = userEvent.setup()
    mockCapabilities.mockRejectedValue(new Error("network down"))
    const onSelectKind = vi.fn()
    const onProLocked = vi.fn()
    renderGallery({ onSelectKind, onProLocked })

    const coreTile = await screen.findByRole("button", { name: /add rss/i })
    expect(coreTile).toBeEnabled()

    await user.click(screen.getByRole("button", { name: /add apple mail \(pro\)/i }))
    expect(onProLocked).toHaveBeenCalledWith("apple_mail")
    expect(onSelectKind).not.toHaveBeenCalled()
  })
})
