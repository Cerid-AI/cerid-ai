// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("@/lib/api", () => ({
  searchForgetAssist: vi.fn(),
  previewForgetItems: vi.fn(),
  forgetSubjects: vi.fn(),
  restoreForget: vi.fn(),
  ForgetHttpError: class extends Error { status = 500 },
  ForgetConflictError: class extends Error {},
}))

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import { ForgetAssistant } from "@/components/settings/forget-assistant"
import { CONVERSATIONS_FORGOTTEN_EVENT } from "@/lib/forget-items-with-undo"
import * as api from "@/lib/api"
import type { AssistResult } from "@/lib/api"

const DOC = "d".repeat(64)
const P1 = `${DOC}_1111111111111111`
const P2 = `${DOC}_2222222222222222`

const GROUPED: AssistResult = {
  status: "grouped", model: "local", reason: "", cloud_model: "", scope: "my old 401k", total: 3,
  groups: [
    {
      title: "401(k) statements", explanation: "They are statements from the old plan.",
      items: [
        { kind: "artifact", id: DOC, store: "knowledge_base", label: "401k-2019.pdf", excerpt: "Plan statement",
          domain: "finance", reason: "matches the wording", score: 0.9,
          passages: [{ kind: "chunk", id: P1, excerpt: "first passage" }, { kind: "chunk", id: P2, excerpt: "second passage" }] },
        { kind: "memory", id: "11111111-2222-3333-4444-555555555555", store: "memories", label: "Has a 401(k) at Acme",
          excerpt: "", domain: "conversations", reason: "a memory close to the wording", score: 0.7, passages: [] },
      ],
    },
    {
      title: "Other matches", explanation: "",
      items: [
        { kind: "conversation", id: "conv-1", store: "conversations", label: "Rolling over the 401k", excerpt: "which plan?",
          domain: "", reason: "mentions 401k", score: 2, passages: [] },
      ],
    },
  ],
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ForgetAssistant /></QueryClientProvider>)
}

async function search(user: ReturnType<typeof userEvent.setup>, text = "my old 401k") {
  await user.type(screen.getByRole("textbox", { name: "What should Cerid forget?" }), text)
  await user.click(screen.getByRole("button", { name: "Find" }))
}

describe("ForgetAssistant", () => {
  beforeEach(() => {
    vi.mocked(api.searchForgetAssist).mockReset()
    vi.mocked(api.previewForgetItems).mockReset()
    vi.mocked(api.forgetSubjects).mockReset()
  })

  it("shows the groups with their explanations, nothing checked", async () => {
    vi.mocked(api.searchForgetAssist).mockResolvedValue(GROUPED)
    const user = userEvent.setup()
    setup()
    await search(user)
    expect(await screen.findByText("They are statements from the old plan.")).toBeInTheDocument()
    expect(api.searchForgetAssist).toHaveBeenCalledWith("my old 401k", false)
    expect(screen.getByRole("checkbox", { name: "Everything in 401(k) statements" })).not.toBeChecked()
    expect(screen.getByRole("button", { name: "Review and forget (0)" })).toBeDisabled()
  })

  it("a group's box takes every item in it, and only the chosen items are reviewed and forgotten", async () => {
    vi.mocked(api.searchForgetAssist).mockResolvedValue(GROUPED)
    vi.mocked(api.previewForgetItems).mockResolvedValue({
      subject: null, title: "", derived_facts: 0, notes: [], out_of_reach: [],
      groups: [{ key: "conversations", default: "checked", items: [{ kind: "conversation", id: "conv-1", label: "Rolling over the 401k" }] }],
    })
    vi.mocked(api.forgetSubjects).mockResolvedValue({ forget_id: "fg_0123456789abcdef", state: "trashed", receipt: null })
    const heard = vi.fn()
    window.addEventListener(CONVERSATIONS_FORGOTTEN_EVENT, heard)
    const user = userEvent.setup()
    setup()
    await search(user)
    await user.click(await screen.findByRole("checkbox", { name: "Everything in 401(k) statements" }))
    expect(screen.getByRole("checkbox", { name: "Everything in 401(k) statements" })).toBeChecked()
    await user.click(screen.getByRole("checkbox", { name: "Everything in Other matches" }))
    await user.click(screen.getByRole("button", { name: "Review and forget (3)" }))
    await waitFor(() => expect(api.previewForgetItems).toHaveBeenCalled())
    expect(vi.mocked(api.previewForgetItems).mock.calls[0][0]).toEqual([
      { kind: "artifact", id: DOC },
      { kind: "memory", id: "11111111-2222-3333-4444-555555555555" },
      { kind: "conversation", id: "conv-1" },
    ])
    await user.click(await screen.findByRole("button", { name: "Move to Trash" }))
    await waitFor(() => expect(api.forgetSubjects).toHaveBeenCalledWith(
      [{ kind: "conversation", id: "conv-1" }], "trash", "agent",
    ))
    await waitFor(() => expect(heard).toHaveBeenCalled())
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toEqual(["conv-1"])
    window.removeEventListener(CONVERSATIONS_FORGOTTEN_EVENT, heard)
  })

  it("can take one passage of a document instead of the whole document", async () => {
    vi.mocked(api.searchForgetAssist).mockResolvedValue(GROUPED)
    vi.mocked(api.previewForgetItems).mockResolvedValue({
      subject: null, title: "", derived_facts: 0, notes: [], out_of_reach: [], groups: [],
    })
    const user = userEvent.setup()
    setup()
    await search(user)
    const passages = await screen.findAllByRole("checkbox", { name: "Only this passage of 401k-2019.pdf" })
    await user.click(passages[1])
    expect(screen.getByRole("checkbox", { name: "Everything in 401(k) statements" })).toHaveAttribute("data-state", "indeterminate")
    await user.click(screen.getByRole("button", { name: "Review and forget (1)" }))
    await waitFor(() => expect(api.previewForgetItems).toHaveBeenCalledWith([{ kind: "chunk", id: P2 }]))
  })

  it("asks before sending anything to the cloud model, and sends only on yes", async () => {
    vi.mocked(api.searchForgetAssist)
      .mockResolvedValueOnce({ ...GROUPED, status: "needs_consent", model: null, cloud_model: "cloud/model-x",
        reason: "The local model is not available." })
      .mockResolvedValueOnce({ ...GROUPED, model: "cloud", cloud_model: "cloud/model-x" })
    const user = userEvent.setup()
    setup()
    await search(user)
    expect(await screen.findByText("cloud/model-x")).toBeInTheDocument()
    expect(api.searchForgetAssist).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole("button", { name: "Send to the cloud model" }))
    await waitFor(() => expect(api.searchForgetAssist).toHaveBeenLastCalledWith("my old 401k", true))
    expect(await screen.findByText(/Sorted by cloud\/model-x, as you allowed/)).toBeInTheDocument()
  })

  it("consent sends the description it was shown, and keeps the picks already made", async () => {
    vi.mocked(api.searchForgetAssist)
      .mockResolvedValueOnce({ ...GROUPED, status: "needs_consent", model: null, cloud_model: "cloud/model-x" })
      .mockResolvedValueOnce({ ...GROUPED, model: "cloud", cloud_model: "cloud/model-x" })
    const user = userEvent.setup()
    setup()
    await search(user)
    await user.click(await screen.findByRole("checkbox", { name: "Everything in Other matches" }))
    const box = screen.getByRole("textbox", { name: "What should Cerid forget?" })
    await user.clear(box)
    await user.type(box, "something else entirely")
    await user.click(screen.getByRole("button", { name: "Send to the cloud model" }))
    await waitFor(() => expect(api.searchForgetAssist).toHaveBeenLastCalledWith("my old 401k", true))
    expect(await screen.findByRole("button", { name: "Review and forget (1)" })).toBeEnabled()
  })

  it("declining the cloud keeps the matches, unsorted", async () => {
    vi.mocked(api.searchForgetAssist).mockResolvedValue({
      ...GROUPED, status: "needs_consent", model: null, cloud_model: "cloud/model-x", reason: "",
    })
    const user = userEvent.setup()
    setup()
    await search(user)
    await user.click(await screen.findByRole("button", { name: "Show them unsorted" }))
    expect(screen.queryByRole("button", { name: "Send to the cloud model" })).not.toBeInTheDocument()
    expect(screen.getByText("401k-2019.pdf")).toBeInTheDocument()
    expect(api.searchForgetAssist).toHaveBeenCalledTimes(1)
  })

  it("says when nothing matches", async () => {
    vi.mocked(api.searchForgetAssist).mockResolvedValue({ ...GROUPED, total: 0, groups: [] })
    const user = userEvent.setup()
    setup()
    await search(user, "zebra")
    expect(await screen.findByText(/Nothing matches/)).toBeInTheDocument()
  })

  it("has no axe violations with results shown", async () => {
    vi.mocked(api.searchForgetAssist).mockResolvedValue(GROUPED)
    const user = userEvent.setup()
    const { container } = setup()
    await search(user)
    await screen.findByText("They are statements from the old plan.")
    expect(await axe(container)).toHaveNoViolations()
  })
})
