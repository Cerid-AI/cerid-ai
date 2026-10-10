// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("@/lib/api", () => ({ previewForgetItems: vi.fn(), undoUpdate: vi.fn(), forgetEarlierVersions: vi.fn() }))

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import { ForgetItemsDialog } from "@/components/kb/forget-items-dialog"
import * as api from "@/lib/api"
import type { ForgetPreview, ForgetSubject } from "@/lib/api"

const DOC = "d".repeat(64)
const CHUNK = `${"e".repeat(64)}_0123456789abcdef`
const PREVIEW: ForgetPreview = {
  subject: null,
  title: "",
  groups: [
    { key: "documents", default: "checked", items: [
      { kind: "artifact", id: DOC, label: "report.pdf", passages: 4, used_by: 2, domain: "finance" },
    ] },
    { key: "passages", default: "checked", items: [
      { kind: "chunk", id: CHUNK, label: "The 2025 limit is $23,500.", document: "notes.md", children: 3 },
    ] },
    { key: "memories", default: "checked", items: [{ kind: "memory", id: "v1", label: "Prefers index funds" }] },
  ],
  derived_facts: 5,
  notes: ["These are all 2 passages of notes.md; forgetting the document instead also removes the facts drawn from it."],
  out_of_reach: [],
}
const SUBJECTS: ForgetSubject[] = [
  { kind: "artifact", id: DOC }, { kind: "chunk", id: CHUNK }, { kind: "memory", id: "v1" },
]

function setup(onConfirm = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={qc}>
      <ForgetItemsDialog subjects={SUBJECTS} open onOpenChange={() => {}} onConfirm={onConfirm} />
    </QueryClientProvider>,
  )
  return { onConfirm, view }
}

describe("ForgetItemsDialog", () => {
  beforeEach(() => { vi.mocked(api.previewForgetItems).mockReset() })

  it("lists documents, passages and memories, all checked, with their notes", async () => {
    vi.mocked(api.previewForgetItems).mockResolvedValue(PREVIEW)
    setup()
    expect(await screen.findByRole("checkbox", { name: "report.pdf" })).toBeChecked()
    expect(api.previewForgetItems).toHaveBeenCalledWith(SUBJECTS)
    expect(screen.getByText("4 passages · cited by 2 conversations")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "The 2025 limit is $23,500." })).toBeChecked()
    expect(screen.getByText("From notes.md · includes 3 smaller passages")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Prefers index funds" })).toBeChecked()
    expect(screen.getByText("And 5 facts drawn only from these documents")).toBeInTheDocument()
    expect(screen.getByText(/These are all 2 passages of notes.md/)).toBeInTheDocument()
  })

  it("sends only what stays checked, in the chosen mode", async () => {
    vi.mocked(api.previewForgetItems).mockResolvedValue(PREVIEW)
    const user = userEvent.setup()
    const { onConfirm } = setup()
    await user.click(await screen.findByRole("checkbox", { name: "Prefers index funds" }))
    await user.click(screen.getByRole("button", { name: "Forget permanently" }))
    expect(onConfirm).toHaveBeenCalledWith("permanent", [{ kind: "artifact", id: DOC }, { kind: "chunk", id: CHUNK }])
  })

  it("lists a memory's earlier versions, checked; unchecking them forgets only the current one", async () => {
    const memory = "m".repeat(64)
    vi.mocked(api.previewForgetItems).mockResolvedValue({
      ...PREVIEW,
      groups: [
        { key: "memories", default: "checked", items: [{ kind: "artifact", id: memory, label: "Prefers light mode" }] },
        { key: "earlier_versions", default: "checked", items: [
          { kind: "artifact", id: "old", label: "Prefers dark mode", version: 1 },
        ] },
      ],
      notes: ["A memory keeps its earlier versions as history."],
    })
    const user = userEvent.setup()
    const { onConfirm } = setup()
    expect(await screen.findByText("Earlier versions")).toBeInTheDocument()
    expect(screen.getByText("Version 1")).toBeInTheDocument()
    await user.click(screen.getByRole("checkbox", { name: "Prefers dark mode" }))
    await user.click(screen.getByRole("button", { name: "Move to Trash" }))
    expect(onConfirm).toHaveBeenCalledWith("trash", [{ kind: "artifact", id: memory }])
  })

  it("offers to undo the last update of a document with versions instead of forgetting them all", async () => {
    vi.mocked(api.previewForgetItems).mockResolvedValue({
      ...PREVIEW,
      groups: [{ key: "documents", default: "checked", items: [
        { kind: "artifact", id: DOC, label: "report.pdf", passages: 4, versions: 3 },
      ] }],
      notes: [],
    })
    vi.mocked(api.undoUpdate).mockResolvedValue({ forget_id: "fg_u", kind: "artifact", id: DOC, version: 2 })
    const user = userEvent.setup()
    const { onConfirm } = setup()
    expect(await screen.findByText("4 passages · 3 versions")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Undo last update" }))
    expect(api.undoUpdate).toHaveBeenCalledWith("artifact", DOC)
    expect(await screen.findByText(/Version 2 is current again/)).toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it("can forget a document's earlier versions and keep the current one", async () => {
    vi.mocked(api.previewForgetItems).mockResolvedValue({
      ...PREVIEW,
      groups: [{ key: "documents", default: "checked", items: [
        { kind: "artifact", id: DOC, label: "report.pdf", passages: 4, versions: 2 },
      ] }],
      notes: [],
    })
    vi.mocked(api.forgetEarlierVersions).mockResolvedValue({ forget_id: "fg_e", state: "trashed", passages: 3 })
    const user = userEvent.setup()
    const { onConfirm } = setup()
    await user.click(await screen.findByRole("button", { name: "Forget earlier versions" }))
    expect(api.forgetEarlierVersions).toHaveBeenCalledWith(DOC, "trash")
    expect(await screen.findByText(/3 earlier passages moved to the Trash/)).toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it("does not offer undo for a document with one version", async () => {
    vi.mocked(api.previewForgetItems).mockResolvedValue(PREVIEW)
    setup()
    await screen.findByRole("checkbox", { name: "report.pdf" })
    expect(screen.queryByRole("button", { name: "Undo last update" })).not.toBeInTheDocument()
  })

  it("says so when everything is already forgotten, and offers nothing to confirm", async () => {
    vi.mocked(api.previewForgetItems).mockResolvedValue({
      ...PREVIEW, groups: PREVIEW.groups.map((g) => ({ ...g, items: [] })), notes: [],
    })
    setup()
    expect(await screen.findByText("These are already forgotten or no longer exist.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Move to Trash" })).toBeDisabled()
  })

  it("reports a preview that failed to load", async () => {
    vi.mocked(api.previewForgetItems).mockRejectedValue(new Error("down"))
    setup()
    expect(await screen.findByText("Couldn't load what will be forgotten.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Forget permanently" })).toBeDisabled()
  })

  it("has no axe violations", async () => {
    vi.mocked(api.previewForgetItems).mockResolvedValue(PREVIEW)
    setup()
    await screen.findByRole("checkbox", { name: "report.pdf" })
    expect(await axe(document.body)).toHaveNoViolations()
  })
})
