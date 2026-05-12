// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@/components/ui/tooltip"
import { VaultConfigSection } from "@/components/settings/vault-config-section"
import type { WatchedFolder } from "@/lib/api/settings"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api/settings", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/settings")>("@/lib/api/settings")
  return {
    ...actual,
    fetchVaultProfile: vi.fn(),
    updateWatchedFolder: vi.fn(),
  }
})

import { fetchVaultProfile, updateWatchedFolder } from "@/lib/api/settings"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeFolder(overrides: Partial<WatchedFolder> = {}): WatchedFolder {
  return {
    id: "abc123",
    path: "/Users/me/vault",
    label: "My Vault",
    enabled: true,
    domain_override: null,
    exclude_patterns: [],
    search_enabled: true,
    is_vault: false,
    vault_config: null,
    last_scanned_at: null,
    stats: { ingested: 0, skipped: 0, errored: 0 },
    created_at: "2026-05-11T00:00:00Z",
    ...overrides,
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("VaultConfigSection", () => {
  it("hides the form when is_vault is false", () => {
    render(<VaultConfigSection folder={makeFolder({ is_vault: false })} onChanged={() => {}} />, { wrapper })
    expect(screen.getByText(/This folder is a vault/i)).toBeInTheDocument()
    // Fields are gated behind the is_vault toggle.
    expect(screen.queryByLabelText(/MOCs folder name/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Daily notes folder name/i)).not.toBeInTheDocument()
  })

  it("shows the form fields when is_vault is true", () => {
    ;(fetchVaultProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      is_vault: true,
      yaml_present: false,
      profile: {
        root_path: "/Users/me/vault",
        mocs_folders: ["mocs"],
        daily_folders: ["daily"],
        templates_folders: ["templates"],
        attachments_folders: ["attachments"],
        skip_folders: [".obsidian"],
        default_domain: "general",
      },
    })

    render(<VaultConfigSection folder={makeFolder({ is_vault: true })} onChanged={() => {}} />, { wrapper })

    expect(screen.getByLabelText(/MOCs folder name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Daily notes folder name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Templates folder name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Attachments folder name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Skip folders/i)).toBeInTheDocument()
  })

  it("calls updateWatchedFolder when the vault toggle is flipped", async () => {
    ;(updateWatchedFolder as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(makeFolder({ is_vault: true }))
    const onChanged = vi.fn()

    render(<VaultConfigSection folder={makeFolder({ is_vault: false })} onChanged={onChanged} />, { wrapper })

    const toggle = screen.getByRole("switch", { name: /Vault mode/i })
    fireEvent.click(toggle)

    await waitFor(() => {
      expect(updateWatchedFolder).toHaveBeenCalledWith("abc123", { is_vault: true })
    })
    expect(onChanged).toHaveBeenCalled()
  })

  it("shows the .cerid-vault.yaml badge when YAML is present", async () => {
    ;(fetchVaultProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      is_vault: true,
      yaml_present: true,
      profile: {
        root_path: "/Users/me/vault",
        mocs_folders: ["yaml-mocs"],
        daily_folders: ["daily"],
        templates_folders: ["templates"],
        attachments_folders: ["attachments"],
        skip_folders: [".obsidian"],
        default_domain: "general",
      },
    })

    render(<VaultConfigSection folder={makeFolder({ is_vault: true })} onChanged={() => {}} />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText(".cerid-vault.yaml")).toBeInTheDocument()
    })
  })

  it("saves the vault config and re-fetches the profile", async () => {
    ;(updateWatchedFolder as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(makeFolder({ is_vault: true }))
    ;(fetchVaultProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      is_vault: true,
      yaml_present: false,
      profile: {
        root_path: "/Users/me/vault",
        mocs_folders: ["my-mocs"],
        daily_folders: ["daily"],
        templates_folders: ["templates"],
        attachments_folders: ["attachments"],
        skip_folders: [],
        default_domain: "general",
      },
    })
    const onChanged = vi.fn()

    render(<VaultConfigSection folder={makeFolder({ is_vault: true })} onChanged={onChanged} />, { wrapper })

    const mocsInput = screen.getByLabelText(/MOCs folder name/i) as HTMLInputElement
    fireEvent.change(mocsInput, { target: { value: "my-mocs" } })

    fireEvent.click(screen.getByRole("button", { name: /Save vault config/i }))

    await waitFor(() => {
      expect(updateWatchedFolder).toHaveBeenCalledWith(
        "abc123",
        expect.objectContaining({
          vault_config: expect.objectContaining({
            mocs_folders: ["my-mocs"],
          }),
        }),
      )
    })
  })
})
