// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mock API module — must be declared before the component import
// ---------------------------------------------------------------------------

const mockFetchStatus = vi.fn()
const mockPutKey = vi.fn()
const mockTestKey = vi.fn()

vi.mock("@/lib/api", () => ({
  fetchOpenRouterKeyStatus: (...args: unknown[]) => mockFetchStatus(...args),
  putOpenRouterKey: (...args: unknown[]) => mockPutKey(...args),
  testOpenRouterKey: (...args: unknown[]) => mockTestKey(...args),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}))

import { OpenRouterKeyField } from "@/components/settings/openrouter-key-field"
import { toast } from "sonner"

// ---------------------------------------------------------------------------
// Wrapper with fresh QueryClient per test
// ---------------------------------------------------------------------------

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("OpenRouterKeyField", () => {
  it('renders "Not configured" when status returns configured=false', async () => {
    mockFetchStatus.mockResolvedValue({ configured: false, last4: null, updated_at: null })

    render(<OpenRouterKeyField />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("Not configured")).toBeInTheDocument()
    })
  })

  it('renders "ending in abcd" when status has last4', async () => {
    mockFetchStatus.mockResolvedValue({
      configured: true,
      last4: "abcd",
      updated_at: null,
    })

    render(<OpenRouterKeyField />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("abcd")).toBeInTheDocument()
    })
    expect(screen.getByText(/Configured — ending in/)).toBeInTheDocument()
  })

  it("input is type=password", async () => {
    mockFetchStatus.mockResolvedValue({ configured: false, last4: null, updated_at: null })

    render(<OpenRouterKeyField />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("Not configured")).toBeInTheDocument()
    })

    const input = screen.getByLabelText("OpenRouter API key (write-only)")
    expect(input).toHaveAttribute("type", "password")
  })

  it("after successful PUT, the draft input is cleared and toast fires", async () => {
    mockFetchStatus.mockResolvedValue({ configured: false, last4: null, updated_at: null })
    mockPutKey.mockResolvedValue({ configured: true, last4: "5678", updated_at: "2026-04-18T12:00:00Z" })

    const user = userEvent.setup()
    render(<OpenRouterKeyField />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("Not configured")).toBeInTheDocument()
    })

    const input = screen.getByLabelText("OpenRouter API key (write-only)")
    await user.type(input, "sk-or-v1-test5678")

    const saveButton = screen.getByRole("button", { name: /Save/i })
    await user.click(saveButton)

    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("OpenRouter key saved")
    })

    // Draft should be cleared after save
    await waitFor(() => {
      expect(input).toHaveValue("")
    })
  })
})
