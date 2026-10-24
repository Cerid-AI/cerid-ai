// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("@/lib/api", () => ({ previewForget: vi.fn() }))

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { ForgetConversationDialog } from "@/components/chat/forget-conversation-dialog"
import * as api from "@/lib/api"
import type { ForgetPreview } from "@/lib/api"

const PREVIEW: ForgetPreview = {
  subject: { kind: "conversation", id: "c1" },
  title: "Tax questions",
  groups: [
    { key: "transcripts", default: "always", items: [
      { kind: "artifact", id: "t1", label: "Chat transcript" }, { kind: "artifact", id: "t2", label: "Chat transcript" },
    ] },
    { key: "memories", default: "checked", items: [
      { kind: "artifact", id: "m1", label: "Owns a 401(k)", shared_with: 0 },
      { kind: "artifact", id: "m2", label: "Prefers index funds", shared_with: 1, default: "unchecked" },
    ] },
    { key: "summary", default: "checked", items: [] },
    { key: "verified_memories", default: "checked", items: [{ kind: "memory", id: "v1", label: "The 2025 limit is $23,500." }] },
    { key: "cited_documents", default: "unchecked", items: [{ kind: "artifact", id: "k1", label: "report.pdf", used_by: 2 }] },
  ],
  derived_facts: 3,
  out_of_reach: ["KB backups made before this forget"],
}

function setup(onConfirm = vi.fn()) {
  const view = render(
    <ForgetConversationDialog conversationId="c1" title="Tax questions" open onOpenChange={() => {}} onConfirm={onConfirm} />,
  )
  return { onConfirm, view }
}

describe("ForgetConversationDialog", () => {
  beforeEach(() => { vi.mocked(api.previewForget).mockReset() })

  it("offers files uploaded in the chat, checked unless shared or already in the knowledge base", async () => {
    vi.mocked(api.previewForget).mockResolvedValue({
      ...PREVIEW,
      groups: [
        ...PREVIEW.groups.slice(0, 4),
        { key: "uploads", default: "checked", items: [
          { kind: "artifact", id: "u1", label: "notes.pdf", shared_with: 0 },
          { kind: "artifact", id: "u2", label: "plan.docx", shared_with: 1, default: "unchecked" },
          { kind: "artifact", id: "u3", label: "old.txt", shared_with: 0, default: "unchecked", existing: true },
        ] },
        PREVIEW.groups[4],
      ],
    })
    const { onConfirm } = setup()
    expect(await screen.findByText("Files uploaded in this chat")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "notes.pdf" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "plan.docx" })).not.toBeChecked()
    expect(screen.getByRole("checkbox", { name: "old.txt" })).not.toBeChecked()
    expect(screen.getByText("Also uploaded in 1 other conversation · forgets the whole document")).toBeInTheDocument()
    expect(screen.getByText("Was in your knowledge base before this chat · forgets the whole document")).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: "Move to Trash" }))
    expect(onConfirm.mock.lastCall?.[1]).toContainEqual({ kind: "artifact", id: "u1" })
    expect(onConfirm.mock.lastCall?.[1]).not.toContainEqual({ kind: "artifact", id: "u2" })
  })

  it("lists what the chat produced with derived items checked and cited documents unchecked", async () => {
    vi.mocked(api.previewForget).mockResolvedValue(PREVIEW)
    setup()
    expect(await screen.findByText("Memories from this chat")).toBeInTheDocument()
    expect(api.previewForget).toHaveBeenCalledWith("c1")
    expect(screen.getByText("Chat transcripts")).toBeInTheDocument()
    expect(screen.getByText(/2 transcripts, always removed with the chat/)).toBeInTheDocument()
    expect(screen.queryByText("Session summary")).not.toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Owns a 401(k)" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "Prefers index funds" })).not.toBeChecked()
    expect(screen.getByText("Also from 1 other conversation")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "The 2025 limit is $23,500." })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "report.pdf" })).not.toBeChecked()
    expect(screen.getByText("Used by 2 other conversations · forgets the whole document")).toBeInTheDocument()
    expect(screen.getByText("And 3 facts derived only from these memories")).toBeInTheDocument()
  })

  it("confirms with only the checked items, in either mode", async () => {
    vi.mocked(api.previewForget).mockResolvedValue(PREVIEW)
    const { onConfirm } = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole("checkbox", { name: "Owns a 401(k)" }))
    await user.click(screen.getByRole("checkbox", { name: "report.pdf" }))
    await user.click(screen.getByRole("button", { name: "Move to Trash" }))
    expect(onConfirm).toHaveBeenLastCalledWith("trash", [
      { kind: "memory", id: "v1" }, { kind: "artifact", id: "k1" },
    ])
    await user.click(screen.getByRole("button", { name: "Forget permanently" }))
    expect(onConfirm).toHaveBeenLastCalledWith("permanent", [
      { kind: "memory", id: "v1" }, { kind: "artifact", id: "k1" },
    ])
  })

  it("offers a plain move to Trash when the preview cannot load", async () => {
    vi.mocked(api.previewForget).mockImplementation(() => Promise.reject(new Error("preview refused")))
    const { onConfirm } = setup()
    const user = userEvent.setup()
    expect(await screen.findByText("Couldn't load what this chat produced.")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Forget permanently" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Move to Trash anyway" }))
    expect(onConfirm).toHaveBeenCalledWith("trash", [])
  })

  it("has no accessibility violations", async () => {
    vi.mocked(api.previewForget).mockResolvedValue(PREVIEW)
    setup()
    const dialog = await screen.findByRole("dialog")
    await screen.findByText("Memories from this chat")
    expect(within(dialog).getByText("Delete this conversation?")).toBeInTheDocument()
    expect(await axe(dialog)).toHaveNoViolations()
  })
})
