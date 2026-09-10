// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react"
import { axe } from "jest-axe"

const queryKB = vi.fn().mockResolvedValue({ results: [{ content: "doc answer" }], total_results: 1, domains_searched: ["general"] })

vi.mock("@/lib/api", () => ({
  uploadFile: vi.fn().mockResolvedValue({ artifact_id: "test-1", filename: "test.pdf" }),
  queryKB: (...args: unknown[]) => queryKB(...args),
}))

vi.mock("@/hooks/use-drag-drop", () => ({
  useDragDrop: () => ({
    isDragOver: false,
    dragHandlers: {
      onDragEnter: vi.fn(),
      onDragLeave: vi.fn(),
      onDragOver: vi.fn(),
      onDrop: vi.fn(),
    },
  }),
}))

import { FirstDocumentStep, type FirstDocState } from "@/components/setup/first-document-step"

const DEFAULT_STATE: FirstDocState = {
  ingested: false,
  queried: false,
  skipped: false,
  documentCount: 0,
}

const onChange = vi.fn<(state: FirstDocState) => void>()

const DEFAULT_QUERY_RESULT = {
  results: [{ content: "doc answer" }],
  total_results: 1,
  domains_searched: ["general"],
}

beforeEach(() => {
  onChange.mockClear()
  queryKB.mockReset()
  queryKB.mockResolvedValue(DEFAULT_QUERY_RESULT)
})

describe("FirstDocumentStep", () => {
  it("shows 'Try It Out' heading", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Try It Out")).toBeInTheDocument()
  })

  it("shows upload zone with drop instruction", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Drop a file or click to upload")).toBeInTheDocument()
  })

  it("shows 'Use sample content' button", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Use sample content")).toBeInTheDocument()
  })

  it("shows supported file type info", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("PDF, TXT, MD, DOCX")).toBeInTheDocument()
  })

  it("has a hidden file input with correct accept types", () => {
    const { container } = render(
      <FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />,
    )
    const fileInput = container.querySelector("input[type='file']")
    expect(fileInput).toBeInTheDocument()
    expect(fileInput).toHaveAttribute("accept", ".pdf,.txt,.md,.docx")
  })

  it("shows Quick start badge next to sample content button", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Quick start")).toBeInTheDocument()
  })

  it("scopes the wizard query to the KB only (external sources disabled)", async () => {
    render(
      <FirstDocumentStep state={{ ...DEFAULT_STATE, ingested: true }} onChange={onChange} />,
    )
    const input = screen.getByPlaceholderText("Or type your own question...")
    fireEvent.change(input, { target: { value: "what is in my doc?" } })
    fireEvent.keyDown(input, { key: "Enter" })

    await waitFor(() => expect(queryKB).toHaveBeenCalled())
    // queryKB(query, domains, topK, conversationMessages, opts)
    const opts = queryKB.mock.calls[0]?.[4] as { contextSources?: { external?: boolean } }
    expect(opts?.contextSources?.external).toBe(false)
  })
})

describe("FirstDocumentStep — query failure copy names the real cause", () => {
  function ask() {
    const input = screen.getByPlaceholderText("Or type your own question...")
    fireEvent.change(input, { target: { value: "what is in my doc?" } })
    fireEvent.keyDown(input, { key: "Enter" })
  }

  it("says no model is configured when there is no provider and local inference is off", async () => {
    queryKB.mockRejectedValue(new Error("[NO_PROVIDER] no model configured"))
    render(
      <FirstDocumentStep
        state={{ ...DEFAULT_STATE, ingested: true }}
        onChange={onChange}
        hasAnsweringModel={false}
      />,
    )
    ask()
    expect(
      await screen.findByText(
        "No model can answer yet — add a provider key or enable local inference in the earlier step.",
      ),
    ).toBeInTheDocument()
  })

  it("surfaces the server detail for an HTTP error when a model is configured", async () => {
    queryKB.mockRejectedValue(new Error("Collection 'general' does not exist"))
    render(
      <FirstDocumentStep
        state={{ ...DEFAULT_STATE, ingested: true }}
        onChange={onChange}
        hasAnsweringModel
      />,
    )
    ask()
    expect(
      await screen.findByText("Collection 'general' does not exist"),
    ).toBeInTheDocument()
  })

  it("keeps the indexing copy for the retry-exhausted empty result", async () => {
    // The component sleeps 300ms then 800ms between retries; drive those with
    // fake timers rather than waiting 1.1s of wall clock per run.
    vi.useFakeTimers()
    try {
      queryKB.mockResolvedValue({ results: [], total_results: 0, domains_searched: ["general"] })
      render(
        <FirstDocumentStep
          state={{ ...DEFAULT_STATE, ingested: true }}
          onChange={onChange}
          hasAnsweringModel
        />,
      )
      ask()
      await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
      expect(queryKB).toHaveBeenCalledTimes(3)
      expect(
        screen.getByText(
          "Query failed — the knowledge base may still be indexing. Try again in a moment.",
        ),
      ).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("FirstDocumentStep — axe-clean", () => {
  it("is axe-clean in the default (choose) phase", async () => {
    const { container } = render(
      <FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in the chat phase (document already ingested)", async () => {
    const { container } = render(
      <FirstDocumentStep state={{ ...DEFAULT_STATE, ingested: true }} onChange={onChange} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
