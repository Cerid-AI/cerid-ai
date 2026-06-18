// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PermissionsStep } from "@/components/setup/permissions-step"

interface PermissionState {
  category: string
  status: string
  required: boolean
  description: string
}

const sampleAll = (overrides: Partial<Record<string, string>> = {}): PermissionState[] => [
  { category: "microphone", status: overrides.microphone ?? "not-determined", required: false, description: "Mic" },
  { category: "calendar", status: overrides.calendar ?? "not-determined", required: false, description: "Cal" },
  { category: "reminders", status: overrides.reminders ?? "not-determined", required: false, description: "Rem" },
  { category: "contacts", status: overrides.contacts ?? "not-determined", required: false, description: "Con" },
  { category: "photos", status: overrides.photos ?? "not-determined", required: false, description: "Photo" },
  { category: "full-disk-access", status: overrides["full-disk-access"] ?? "denied", required: false, description: "FDA" },
]

const mockGetAll = vi.fn()
const mockRequest = vi.fn()
const mockOpenExternal = vi.fn()

beforeEach(() => {
  mockGetAll.mockReset()
  mockRequest.mockReset()
  mockOpenExternal.mockReset()
  mockGetAll.mockResolvedValue(sampleAll())
  mockOpenExternal.mockResolvedValue({ success: true })
  ;(window as unknown as { cerid: object }).cerid = {
    permissions: {
      getAll: mockGetAll,
      get: vi.fn(),
      request: mockRequest,
    },
    app: { openExternal: mockOpenExternal },
  }
})

afterEach(() => {
  delete (window as unknown as { cerid?: object }).cerid
})

describe("PermissionsStep", () => {
  it("renders web-only fallback when window.cerid is absent", async () => {
    delete (window as unknown as { cerid?: object }).cerid
    render(<PermissionsStep />)
    expect(await screen.findByText(/desktop app/i)).toBeInTheDocument()
  })

  it("lists all six permission categories", async () => {
    render(<PermissionsStep />)
    expect(await screen.findByTestId("permission-row-microphone")).toBeInTheDocument()
    expect(screen.getByTestId("permission-row-calendar")).toBeInTheDocument()
    expect(screen.getByTestId("permission-row-reminders")).toBeInTheDocument()
    expect(screen.getByTestId("permission-row-contacts")).toBeInTheDocument()
    expect(screen.getByTestId("permission-row-photos")).toBeInTheDocument()
    expect(screen.getByTestId("permission-row-full-disk-access")).toBeInTheDocument()
  })

  it("clicking Grant on not-determined microphone calls request()", async () => {
    mockRequest.mockResolvedValue({ category: "microphone", status: "granted", required: false, description: "Mic" })
    mockGetAll
      .mockResolvedValueOnce(sampleAll())
      .mockResolvedValueOnce(sampleAll({ microphone: "granted" }))
    const user = userEvent.setup()
    render(<PermissionsStep />)
    await user.click(await screen.findByTestId("permission-grant-microphone"))
    await waitFor(() => {
      expect(mockRequest).toHaveBeenCalledWith("microphone")
    })
  })

  it("clicking Grant on denied permission opens System Settings instead of requesting", async () => {
    mockGetAll.mockResolvedValue(sampleAll({ calendar: "denied" }))
    const user = userEvent.setup()
    render(<PermissionsStep />)
    const btn = await screen.findByTestId("permission-grant-calendar")
    await user.click(btn)
    await waitFor(() => {
      expect(mockOpenExternal).toHaveBeenCalled()
      expect(mockOpenExternal.mock.calls[0][0]).toContain("Privacy_Calendars")
    })
    expect(mockRequest).not.toHaveBeenCalled()
  })

  it("Full Disk Access always opens System Settings (no programmatic prompt)", async () => {
    mockGetAll.mockResolvedValue(sampleAll())
    const user = userEvent.setup()
    render(<PermissionsStep />)
    await user.click(await screen.findByTestId("permission-grant-full-disk-access"))
    await waitFor(() => {
      expect(mockOpenExternal).toHaveBeenCalled()
      expect(mockOpenExternal.mock.calls[0][0]).toContain("Privacy_AllFiles")
    })
  })

  it("FDA denied state shows relaunch warning", async () => {
    mockGetAll.mockResolvedValue(sampleAll({ "full-disk-access": "denied" }))
    render(<PermissionsStep />)
    expect(await screen.findByText(/relaunch Cerid after granting Full Disk Access/i)).toBeInTheDocument()
  })

  it("granted permission shows green check + no button", async () => {
    mockGetAll.mockResolvedValue(sampleAll({ microphone: "granted" }))
    render(<PermissionsStep />)
    await screen.findByTestId("permission-row-microphone")
    expect(screen.queryByTestId("permission-grant-microphone")).toBeNull()
    const row = screen.getByTestId("permission-row-microphone")
    expect(row.textContent).toMatch(/granted/)
  })

  it("Skip and Continue buttons fire callbacks", async () => {
    const onSkip = vi.fn()
    const onContinue = vi.fn()
    const user = userEvent.setup()
    render(<PermissionsStep onSkip={onSkip} onContinue={onContinue} />)
    await screen.findByTestId("permission-row-microphone")
    await user.click(screen.getByRole("button", { name: /^skip$/i }))
    expect(onSkip).toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: /^continue$/i }))
    expect(onContinue).toHaveBeenCalled()
  })
})
