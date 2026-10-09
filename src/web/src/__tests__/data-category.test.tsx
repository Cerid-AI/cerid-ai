// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import DataCategory from "@/components/settings/categories/data"
import type { ServerSettings } from "@/lib/types"

const settings = { forget_trash_days: 30 } as unknown as ServerSettings

const TRASH = [
  { forget_id: "fg_aaaaaaaaaaaaaaaa", at: "2026-10-08T10:00:00Z", requested_by: "api", purge_started: false,
    subjects: [{ kind: "conversation", id: "c1", label: "Tax questions" }, { kind: "artifact", id: "a1", label: "" }] },
  { forget_id: "fg_bbbbbbbbbbbbbbbb", at: "2026-10-07T10:00:00Z", requested_by: "api", purge_started: true,
    subjects: [{ kind: "conversation", id: "c2", label: "" }] },
]
const RECEIPTS = [
  { forget_id: "fg_cccccccccccccccc", at: "2026-10-08T09:00:00Z", requested_by: "ui",
    subjects: { conversation: 1, artifact: 2 }, status: "done" },
  { forget_id: "fg_dddddddddddddddd", at: "2026-10-06T09:00:00Z", requested_by: "retention",
    subjects: { conversation: 1 }, status: "pending" },
]
const RECEIPT = {
  forget_id: "fg_cccccccccccccccc", at: "2026-10-08T09:00:00Z", requested_by: "ui", subjects: [],
  adapters: { artifacts: { status: "done", removed: 2 }, transcripts: { status: "done", removed: 4 } },
  out_of_reach: ["KB backups made before this forget"],
}

function res(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) })
}

function apis(overrides: Record<string, () => Promise<unknown>> = {}) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    for (const [k, fn] of Object.entries(overrides)) if (url.includes(k)) return fn()
    if (url.endsWith("/forget/trash")) return res({ items: TRASH })
    if (url.endsWith("/forget/receipts")) return res({ items: RECEIPTS })
    if (url.includes("/forget/receipts/")) return res(RECEIPT)
    if (url.includes("/restore") && init?.method === "POST") return res({ restored: 1 })
    if (url.endsWith("/forget/trash/empty")) return res({ purged: ["fg_aaaaaaaaaaaaaaaa"] })
    return res({})
  })
}

function renderPage(patch = vi.fn().mockResolvedValue({ ok: true }), s = settings) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <DataCategory settings={s} patch={patch} onRefresh={vi.fn()} />
    </QueryClientProvider>,
  )
  return { patch }
}

beforeEach(() => { vi.restoreAllMocks() })

describe("Settings → Data", () => {
  it("lists the Trash with labels, dates and restore state", async () => {
    vi.stubGlobal("fetch", apis())
    renderPage()
    const trash = await screen.findByRole("region", { name: "Trash" })
    expect((await within(trash).findAllByText("Tax questions")).length).toBeGreaterThan(0)
    expect(within(trash).getAllByText("Untitled conversation").length).toBeGreaterThan(0)
    const restoreButtons = within(trash).getAllByRole("button", { name: /^Restore/ })
    expect(restoreButtons[0]).toBeEnabled()
    expect(restoreButtons[1]).toBeDisabled()
    expect(within(trash).getByText("Erasing — can't be restored")).toBeInTheDocument()
    expect(within(trash).getByText(/1 conversation, 1 document or memory$/)).toBeInTheDocument()
  })

  it("shows an empty Trash and a load error with Retry", async () => {
    vi.stubGlobal("fetch", apis({ "/forget/trash": () => res({ items: [] }) }))
    renderPage()
    expect(await screen.findByText("Trash is empty")).toBeInTheDocument()
  })

  it("shows a load error with Retry", async () => {
    vi.stubGlobal("fetch", apis({ "/forget/trash": () => res({ detail: "boom" }, 500) }))
    renderPage()
    const trash = await screen.findByRole("region", { name: "Trash" })
    expect(await within(trash).findByRole("button", { name: "Retry" })).toBeInTheDocument()
  })

  it("restores a forget, and explains a refused restore", async () => {
    const fetchMock = apis()
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    renderPage()
    const trash = await screen.findByRole("region", { name: "Trash" })
    await within(trash).findAllByText("Tax questions")
    await user.click(within(trash).getAllByRole("button", { name: /^Restore/ })[0])
    await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/forget/fg_aaaaaaaaaaaaaaaa/restore") && i?.method === "POST")).toBe(true))
    expect(await screen.findByText("Restored. It will reappear in your chats.")).toBeInTheDocument()

    vi.stubGlobal("fetch", apis({ "/restore": () => res({ detail: "purge started" }, 409) }))
    await user.click(within(trash).getAllByRole("button", { name: /^Restore/ })[0])
    expect(await screen.findByText("This has started erasing and can't be restored.")).toBeInTheDocument()
  })

  it("empties the Trash after confirmation", async () => {
    const fetchMock = apis()
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    renderPage()
    const trash = await screen.findByRole("region", { name: "Trash" })
    await within(trash).findAllByText("Tax questions")
    await user.click(within(trash).getByRole("button", { name: "Empty Trash" }))
    const dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText("Empty the Trash?")).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Empty Trash" }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/forget/trash/empty") && i?.method === "POST")).toBe(true))
  })

  it("lists receipts and opens one with per-store counts", async () => {
    vi.stubGlobal("fetch", apis())
    const user = userEvent.setup()
    renderPage()
    const receipts = await screen.findByRole("region", { name: "Receipts" })
    expect(await within(receipts).findByText("Erased")).toBeInTheDocument()
    expect(within(receipts).getByText("Still erasing — retried automatically")).toBeInTheDocument()
    await user.click(within(receipts).getAllByRole("button", { name: /^Show receipt/ })[0])
    expect(await within(receipts).findByText("transcripts: removed 4")).toBeInTheDocument()
    expect(within(receipts).getByText("KB backups made before this forget")).toBeInTheDocument()
  })

  it("sets the auto-empty window", async () => {
    vi.stubGlobal("fetch", apis())
    const { patch } = renderPage(undefined, { forget_trash_days: 0 } as unknown as ServerSettings)
    expect(await screen.findByText("Never empty automatically")).toBeInTheDocument()
    const slider = screen.getByRole("slider")
    slider.focus()
    await userEvent.keyboard("{ArrowRight}")
    expect(patch).toHaveBeenCalledWith({ forget_trash_days: 1 })
  })

  it("has no accessibility violations", async () => {
    vi.stubGlobal("fetch", apis())
    const { container } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DataCategory settings={settings} patch={vi.fn()} onRefresh={vi.fn()} />
      </QueryClientProvider>,
    )
    await screen.findAllByText("Tax questions")
    expect(await axe(container)).toHaveNoViolations()
  })
})
