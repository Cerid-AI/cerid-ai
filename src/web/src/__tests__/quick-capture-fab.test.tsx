// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Phase B Day 12 — Quick-capture FAB tests. Visible on every pane;
// ⌘⇧N global; mode switcher (Note/URL/Upload); Escape closes.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { NavigationProvider } from "@/contexts/navigation-context"
import { QuickCaptureFab } from "@/components/quick-capture/quick-capture-fab"
import { ingestUrl } from "@/lib/api/kb"

vi.mock("@/lib/api/kb", () => ({
  uploadFile: vi.fn(async () => ({ artifact_id: "test", filename: "test.md" })),
  ingestUrl: vi.fn(async () => ({ status: "ok", artifact_id: "test-url" })),
}))

function renderFab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <NavigationProvider activePane="chat" onPaneChange={() => {}}>
        <QuickCaptureFab />
      </NavigationProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("QuickCaptureFab", () => {
  it("renders only the FAB button by default (modal closed)", () => {
    renderFab()
    expect(screen.getByRole("button", { name: /quick capture/i })).toBeInTheDocument()
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("clicking the FAB opens the modal", () => {
    renderFab()
    fireEvent.click(screen.getByRole("button", { name: /quick capture/i }))
    expect(screen.getByRole("dialog", { name: /quick capture/i })).toBeInTheDocument()
  })

  it("global ⌘⇧N opens the modal", () => {
    renderFab()
    fireEvent.keyDown(document, { key: "n", metaKey: true, shiftKey: true })
    expect(screen.getByRole("dialog", { name: /quick capture/i })).toBeInTheDocument()
  })

  it("global Ctrl+⇧N also opens the modal", () => {
    renderFab()
    fireEvent.keyDown(document, { key: "N", ctrlKey: true, shiftKey: true })
    expect(screen.getByRole("dialog", { name: /quick capture/i })).toBeInTheDocument()
  })

  it("Escape closes the modal", () => {
    renderFab()
    fireEvent.click(screen.getByRole("button", { name: /quick capture/i }))
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("offers Note / URL / Upload mode tabs", () => {
    renderFab()
    fireEvent.click(screen.getByRole("button", { name: /quick capture/i }))
    expect(screen.getByRole("tab", { name: /note/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /url/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /upload/i })).toBeInTheDocument()
  })

  it("Note save button is disabled with empty input", () => {
    renderFab()
    fireEvent.click(screen.getByRole("button", { name: /quick capture/i }))
    const saveBtn = screen.getByRole("button", { name: /save note/i })
    expect(saveBtn).toBeDisabled()
  })

  it("Note save button is enabled with non-empty input", () => {
    renderFab()
    fireEvent.click(screen.getByRole("button", { name: /quick capture/i }))
    const textarea = screen.getByLabelText(/note content/i)
    fireEvent.change(textarea, { target: { value: "test note" } })
    const saveBtn = screen.getByRole("button", { name: /save note/i })
    expect(saveBtn).not.toBeDisabled()
  })

  it("URL mode: submitting calls ingestUrl and renders success status", async () => {
    renderFab()
    fireEvent.click(screen.getByRole("button", { name: /quick capture/i }))
    fireEvent.click(screen.getByRole("tab", { name: /url/i }))
    const input = screen.getByLabelText(/url to ingest/i)
    fireEvent.change(input, { target: { value: "https://example.com/article" } })
    fireEvent.click(screen.getByRole("button", { name: /ingest url/i }))
    await waitFor(() => expect(ingestUrl).toHaveBeenCalledWith("https://example.com/article"))
    await waitFor(() => expect(screen.getByText("Captured")).toBeInTheDocument())
  })

  it("URL mode: a failed ingest renders the error message and keeps the modal open", async () => {
    vi.mocked(ingestUrl).mockRejectedValueOnce(new Error("URL is not fetchable: timed out"))
    renderFab()
    fireEvent.click(screen.getByRole("button", { name: /quick capture/i }))
    fireEvent.click(screen.getByRole("tab", { name: /url/i }))
    const input = screen.getByLabelText(/url to ingest/i)
    fireEvent.change(input, { target: { value: "https://example.com/dead" } })
    fireEvent.click(screen.getByRole("button", { name: /ingest url/i }))
    await waitFor(() =>
      expect(screen.getByText("URL is not fetchable: timed out")).toBeInTheDocument(),
    )
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })
})
