// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import React from "react"
import { KindSpecificFields, SourceConfigForm } from "@/components/sources/source-config-form"
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
// Folder source — ADD mode (wizard): editable path + import-mode radio
// ---------------------------------------------------------------------------

/** Stateful harness — KindSpecificFields is controlled via config/onConfig. */
function FolderAddHarness({
  initial = {},
  allowedRoots,
  onConfigSpy,
}: {
  initial?: Record<string, unknown>
  allowedRoots?: string[]
  onConfigSpy?: (v: Record<string, unknown>) => void
}) {
  const [config, setConfig] = React.useState<Record<string, unknown>>(initial)
  return (
    <KindSpecificFields
      kind="folder"
      providers={[]}
      config={config}
      onConfig={(v) => {
        onConfigSpy?.(v)
        setConfig(v)
      }}
      allowedRoots={allowedRoots}
    />
  )
}

describe("KindSpecificFields — folder ADD mode", () => {
  it("renders an editable path input and writes config.path", () => {
    const spy = vi.fn()
    render(<FolderAddHarness onConfigSpy={spy} />)
    const input = screen.getByLabelText(/folder path/i)
    fireEvent.change(input, { target: { value: "/archive/notes" } })
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ path: "/archive/notes" }))
    expect((screen.getByLabelText(/folder path/i) as HTMLInputElement).value).toBe("/archive/notes")
  })

  it("explains container-path semantics and lists the allowed roots", () => {
    render(<FolderAddHarness allowedRoots={["/archive", "/root/cerid-archive"]} />)
    expect(screen.getByText(/inside the Cerid container/i)).toBeInTheDocument()
    expect(screen.getByText(/allowed roots:/i)).toHaveTextContent("/archive, /root/cerid-archive")
  })

  it("offers watch vs one-time import modes, defaulting to watch", () => {
    const spy = vi.fn()
    render(<FolderAddHarness onConfigSpy={spy} />)
    const watch = screen.getByRole("radio", { name: /watch folder/i })
    const once = screen.getByRole("radio", { name: /one-time import/i })
    expect(watch).toBeChecked()
    expect(once).not.toBeChecked()
    fireEvent.click(once)
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ import_mode: "once" }))
    expect(screen.getByRole("radio", { name: /one-time import/i })).toBeChecked()
  })

  it("is axe-clean in add mode", async () => {
    const { container } = render(<FolderAddHarness allowedRoots={["/archive"]} />)
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("KindSpecificFields — folder EDIT mode keeps path immutable", () => {
  it("does not render the editable path input in edit mode", () => {
    render(
      <KindSpecificFields
        kind="folder"
        providers={[]}
        config={{ path: "/data/notes" }}
        onConfig={() => {}}
        editMode
      />,
    )
    expect(screen.queryByLabelText(/folder path/i)).not.toBeInTheDocument()
    expect(screen.getByText("/data/notes")).toBeInTheDocument()
    expect(screen.getByText(/cannot be changed after creation/i)).toBeInTheDocument()
    // Import mode is a creation-time choice; hidden in edit mode.
    expect(screen.queryByRole("radio")).not.toBeInTheDocument()
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

// ---------------------------------------------------------------------------
// axe-clean — one assertion per visually-distinct config-kind variant this
// form renders (no fetch cycle; the per-kind branch is the distinct state).
// ---------------------------------------------------------------------------

describe("SourceConfigForm — axe-clean", () => {
  it("is axe-clean for an rss source", async () => {
    const source = makeSource({ kind: "rss", config: { url: "https://example.com/feed.xml" } })
    const { container } = render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean for a webhook source with a redacted secret", async () => {
    const source = makeSource({
      kind: "webhook",
      config: { require_hmac: true, hmac_secret: "***redacted***" },
    })
    const { container } = render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean for a folder source", async () => {
    const source = makeSource({
      kind: "folder",
      config: { path: "/home/user/notes", label: "Notes", exclude_patterns: ["*.tmp"] },
    })
    const { container } = render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean for a source with a read-only provider (edit mode)", async () => {
    const source = makeSource({ kind: "chat_capture", config: { provider: "discord" } })
    const { container } = render(<SourceConfigForm source={source} onSaved={() => {}} />, { wrapper: wrap() })
    expect(await axe(container)).toHaveNoViolations()
  })
})
