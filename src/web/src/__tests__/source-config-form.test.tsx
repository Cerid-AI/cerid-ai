// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"
import { SourceConfigForm } from "@/components/sources/source-config-form"
import type { SourceRecord } from "@/lib/api/sources"

const mockPatchSourceConfig = vi.fn()

vi.mock("@/lib/api/sources", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/sources")>()),
  patchSourceConfig: (...args: unknown[]) => mockPatchSourceConfig(...args),
}))

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function makeSource(overrides: Partial<SourceRecord> = {}): SourceRecord {
  return {
    id: "src:1",
    kind: "rss",
    family: "rss",
    display_name: "My Feed",
    tier: "core",
    status: "connected",
    config: {},
    sync_cursor: {},
    total_artifacts: 0,
    total_chunks: 0,
    total_edges: 0,
    total_artifacts_24h: 0,
    connection_time_ms: null,
    last_sync_at: null,
    created_at: null,
    last_error: null,
    quality_floor: 0,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPatchSourceConfig.mockResolvedValue({})
})

// ---------------------------------------------------------------------------
// RSS source — URL pre-filled and editable
// ---------------------------------------------------------------------------

describe("SourceConfigForm — rss source", () => {
  it("pre-fills the URL field from source.config.url", () => {
    const source = makeSource({ kind: "rss", config: { url: "https://example.com/feed.xml" } })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    const input = screen.getByLabelText(/feed url/i)
    expect((input as HTMLInputElement).value).toBe("https://example.com/feed.xml")
  })

  it("calls patchSourceConfig with only the changed field on Save", async () => {
    const source = makeSource({ kind: "rss", config: { url: "https://old.example.com/feed.xml", label: "unchanged-label" } })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    const input = screen.getByLabelText(/feed url/i)
    fireEvent.change(input, { target: { value: "https://new.example.com/feed.xml" } })
    fireEvent.click(screen.getByRole("button", { name: /save/i }))
    await waitFor(() => expect(mockPatchSourceConfig).toHaveBeenCalledOnce())
    const [, patchArg] = mockPatchSourceConfig.mock.calls[0] as [string, Record<string, unknown>]
    // Changed field IS present
    expect(patchArg.url).toBe("https://new.example.com/feed.xml")
    // Unchanged field is ABSENT from the diff
    expect(Object.prototype.hasOwnProperty.call(patchArg, "label")).toBe(false)
  })

  it("calls onSaved after successful save", async () => {
    const onSaved = vi.fn()
    const source = makeSource({ kind: "rss", config: { url: "https://example.com/feed.xml" } })
    render(<SourceConfigForm source={source} onSaved={onSaved} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("button", { name: /save/i }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce())
  })
})

// ---------------------------------------------------------------------------
// Redacted secrets — round-trip the mask when untouched
// ---------------------------------------------------------------------------

describe("SourceConfigForm — redacted secret round-trip", () => {
  it("shows a placeholder for redacted secrets", () => {
    const source = makeSource({
      kind: "webhook",
      config: { require_hmac: true, hmac_secret: "***redacted***" },
    })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    // The placeholder text for redacted values
    expect(screen.getByPlaceholderText(/unchanged/i)).toBeInTheDocument()
  })

  it("omits the redacted secret from the diff when the field is left untouched", async () => {
    const source = makeSource({
      kind: "webhook",
      config: { require_hmac: false, hmac_secret: "***redacted***" },
    })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("button", { name: /save/i }))
    await waitFor(() => expect(mockPatchSourceConfig).toHaveBeenCalledOnce())
    // Untouched redacted secret equals the seed value, so it's excluded from the diff.
    // The backend preserves the stored secret when the field is absent.
    const [, patchArg] = mockPatchSourceConfig.mock.calls[0] as [string, Record<string, unknown>]
    expect(Object.prototype.hasOwnProperty.call(patchArg, "hmac_secret")).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Folder source — label/exclude fields; path is read-only
// ---------------------------------------------------------------------------

describe("SourceConfigForm — folder source", () => {
  it("shows label and exclude_patterns fields", () => {
    const source = makeSource({
      kind: "folder",
      config: { path: "/home/user/notes", label: "Notes", exclude_patterns: ["*.tmp"] },
    })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    expect(screen.getByLabelText(/label/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/exclude patterns/i)).toBeInTheDocument()
  })

  it("path is displayed as read-only (not an editable input)", () => {
    const source = makeSource({
      kind: "folder",
      config: { path: "/home/user/notes" },
    })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    // Path is shown as text, not an editable input
    expect(screen.getByText("/home/user/notes")).toBeInTheDocument()
    // The path field should not be an editable input
    const pathInputs = screen.queryAllByDisplayValue("/home/user/notes")
    // If it's an input it would have a display value — a read-only display element won't
    for (const el of pathInputs) {
      expect((el as HTMLInputElement).readOnly).toBe(true)
    }
  })

  it("label field is seeded from source.config.label", () => {
    const source = makeSource({
      kind: "folder",
      config: { path: "/notes", label: "My Notes" },
    })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    const labelInput = screen.getByLabelText(/label/i)
    expect((labelInput as HTMLInputElement).value).toBe("My Notes")
  })
})

// ---------------------------------------------------------------------------
// Provider is read-only on edit
// ---------------------------------------------------------------------------

describe("SourceConfigForm — provider read-only on edit", () => {
  it("does not render a provider picker (provider is immutable on edit)", () => {
    const source = makeSource({ kind: "chat_capture", config: { provider: "slack" } })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    // No select/combobox for provider — it's read-only on edit
    expect(screen.queryByRole("combobox", { name: /provider/i })).not.toBeInTheDocument()
  })

  it("shows provider as a static label if present in config", () => {
    const source = makeSource({ kind: "chat_capture", config: { provider: "discord" } })
    render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    expect(screen.getByText(/discord/i)).toBeInTheDocument()
  })
})
