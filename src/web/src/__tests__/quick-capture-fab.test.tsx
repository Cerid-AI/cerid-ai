// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Phase B Day 12 — Quick-capture FAB tests. Visible on every pane;
// ⌘⇧N global; mode switcher (Note/URL/Upload); Escape closes.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { NavigationProvider } from "@/contexts/navigation-context"
import { QuickCaptureFab } from "@/components/quick-capture/quick-capture-fab"

vi.mock("@/lib/api/kb", () => ({
  uploadFile: vi.fn(async () => ({ artifact_id: "test", filename: "test.md" })),
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
})
