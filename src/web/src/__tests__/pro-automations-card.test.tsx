// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ProAutomationsCard } from "@/components/settings/pro-automations-card"

const mockList = vi.fn()
const mockUpdate = vi.fn()
const mockReset = vi.fn()
const mockRunNow = vi.fn()

vi.mock("@/lib/api/settings", () => ({
  listProAutomations: (...a: unknown[]) => mockList(...a),
  updateProAutomation: (...a: unknown[]) => mockUpdate(...a),
  resetProAutomation: (...a: unknown[]) => mockReset(...a),
  runProAutomationNow: (...a: unknown[]) => mockRunNow(...a),
}))

const sample = (overrides: Record<string, unknown> = {}) => [
  {
    feature: "inbox_triage",
    display_name: "Inbox Triage",
    description: "Gmail + Outlook categorization",
    feature_flag: "inbox_triage",
    feature_flag_enabled: true,
    enabled: false,
    schedule: "*/15 * * * *",
    default_schedule: "*/15 * * * *",
    cadence_presets: [
      { label: "Off", cron: "" },
      { label: "Every 15 minutes", cron: "*/15 * * * *" },
      { label: "Every hour", cron: "0 * * * *" },
    ],
    ...overrides,
  },
  {
    feature: "daily_digest",
    display_name: "Daily Digest",
    description: "LLM summary",
    feature_flag: "daily_digest",
    feature_flag_enabled: true,
    enabled: true,
    schedule: "0 7 * * *",
    default_schedule: "0 7 * * *",
    cadence_presets: [
      { label: "Off", cron: "" },
      { label: "Morning (7 AM UTC)", cron: "0 7 * * *" },
    ],
  },
]

beforeEach(() => {
  mockList.mockReset()
  mockUpdate.mockReset()
  mockReset.mockReset()
  mockRunNow.mockReset()
  mockList.mockResolvedValue(sample())
})

describe("ProAutomationsCard", () => {
  it("lists both automations when loaded", async () => {
    render(<ProAutomationsCard tier="pro" />)
    expect(await screen.findByTestId("pro-automation-inbox_triage")).toBeInTheDocument()
    expect(screen.getByTestId("pro-automation-daily_digest")).toBeInTheDocument()
  })

  it("shows active badge for enabled automation", async () => {
    render(<ProAutomationsCard tier="pro" />)
    await screen.findByTestId("pro-automation-daily_digest")
    const row = screen.getByTestId("pro-automation-daily_digest")
    expect(row.textContent).toContain("active")
  })

  it("toggle calls update API with inverted value", async () => {
    mockUpdate.mockResolvedValue({ ...sample()[0], enabled: true })
    const user = userEvent.setup()
    render(<ProAutomationsCard tier="pro" />)
    await user.click(await screen.findByTestId("pro-automation-toggle-inbox_triage"))
    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith("inbox_triage", { enabled: true })
    })
  })

  it("cadence select calls update with new cron", async () => {
    mockUpdate.mockResolvedValue({ ...sample()[0], schedule: "0 * * * *" })
    render(<ProAutomationsCard tier="pro" />)
    const select = await screen.findByTestId("pro-automation-schedule-inbox_triage")
    fireEvent.change(select, { target: { value: "0 * * * *" } })
    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith("inbox_triage", { schedule: "0 * * * *" })
    })
  })

  it("Run now triggers the agent and surfaces detail", async () => {
    mockRunNow.mockResolvedValue({
      feature: "inbox_triage",
      triggered: true,
      detail: "triaged 5 threads",
      result: null,
    })
    const user = userEvent.setup()
    render(<ProAutomationsCard tier="pro" />)
    await user.click(await screen.findByTestId("pro-automation-run-inbox_triage"))
    await waitFor(() => {
      expect(mockRunNow).toHaveBeenCalledWith("inbox_triage")
    })
    // Last run detail surfaces in the cadence row
    expect(await screen.findByText(/triaged 5 threads/)).toBeInTheDocument()
  })

  it("community tier renders lock overlay", async () => {
    render(<ProAutomationsCard tier="community" />)
    await screen.findByTestId("pro-automation-inbox_triage")
    expect(screen.getByTestId("pro-automations-locked-overlay")).toBeInTheDocument()
  })

  it("Pro tier shows no overlay", async () => {
    render(<ProAutomationsCard tier="pro" />)
    await screen.findByTestId("pro-automation-inbox_triage")
    expect(screen.queryByTestId("pro-automations-locked-overlay")).toBeNull()
  })

  it("disabled feature flag adds inline warning", async () => {
    mockList.mockResolvedValue([
      { ...sample()[0], feature_flag_enabled: false },
      sample()[1],
    ])
    render(<ProAutomationsCard tier="pro" />)
    await screen.findByTestId("pro-automation-inbox_triage")
    expect(screen.getByText(/Feature flag/)).toBeInTheDocument()
  })

  it("custom (non-preset) cron renders as Custom option", async () => {
    mockList.mockResolvedValue([
      { ...sample()[0], schedule: "13 7 * * 2" },
      sample()[1],
    ])
    render(<ProAutomationsCard tier="pro" />)
    const select = await screen.findByTestId("pro-automation-schedule-inbox_triage") as HTMLSelectElement
    expect(select.value).toBe("13 7 * * 2")
    expect(select.options[0].textContent).toContain("Custom")
  })

  it("surfaces error from list API", async () => {
    mockList.mockRejectedValue(new Error("backend down"))
    render(<ProAutomationsCard tier="pro" />)
    expect(await screen.findByRole("alert")).toHaveTextContent(/backend down/)
  })

  it("does not throw when listProAutomations resolves with null body.automations", async () => {
    // Simulate CI fixture or backend contract violation returning null
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ automations: null }),
    } as Response)

    try {
      expect(() => render(<ProAutomationsCard tier="pro" />)).not.toThrow()
      // Card renders in empty state — no automation rows
      await screen.findByTestId("pro-automations-card")
      expect(screen.queryByTestId(/pro-automation-row-/)).not.toBeInTheDocument()
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
