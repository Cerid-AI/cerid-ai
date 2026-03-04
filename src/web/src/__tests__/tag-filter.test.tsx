// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TagFilter } from "@/components/kb/tag-filter"

vi.mock("@/lib/api", () => ({
  fetchTagSuggestions: vi.fn().mockResolvedValue([]),
}))

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe("TagFilter", () => {
  it("renders the tag filter input with placeholder", () => {
    render(
      <TagFilter activeTags={[]} onToggleTag={vi.fn()} />,
      { wrapper },
    )
    expect(screen.getByPlaceholderText("Filter by tag...")).toBeInTheDocument()
  })

  it("renders active tag pills with remove buttons", () => {
    render(
      <TagFilter activeTags={["fastapi", "auth"]} onToggleTag={vi.fn()} />,
      { wrapper },
    )
    expect(screen.getByText("fastapi")).toBeInTheDocument()
    expect(screen.getByText("auth")).toBeInTheDocument()
    expect(screen.getByLabelText("Remove fastapi filter")).toBeInTheDocument()
    expect(screen.getByLabelText("Remove auth filter")).toBeInTheDocument()
  })

  it("calls onToggleTag when a tag remove button is clicked", async () => {
    const user = userEvent.setup()
    const onToggleTag = vi.fn()
    render(
      <TagFilter activeTags={["fastapi"]} onToggleTag={onToggleTag} />,
      { wrapper },
    )
    await user.click(screen.getByLabelText("Remove fastapi filter"))
    expect(onToggleTag).toHaveBeenCalledWith("fastapi")
  })

  it("shows Clear button when tags are active", () => {
    render(
      <TagFilter activeTags={["fastapi", "auth"]} onToggleTag={vi.fn()} />,
      { wrapper },
    )
    expect(screen.getByText("Clear")).toBeInTheDocument()
  })

  it("does not show tag pills or Clear button when no tags active", () => {
    render(
      <TagFilter activeTags={[]} onToggleTag={vi.fn()} />,
      { wrapper },
    )
    expect(screen.queryByText("Clear")).not.toBeInTheDocument()
  })

  it("calls onToggleTag for each active tag when Clear is clicked", async () => {
    const user = userEvent.setup()
    const onToggleTag = vi.fn()
    render(
      <TagFilter activeTags={["fastapi", "auth"]} onToggleTag={onToggleTag} />,
      { wrapper },
    )
    await user.click(screen.getByText("Clear"))
    // forEach passes (value, index, array) — assert the first argument of each call
    expect(onToggleTag).toHaveBeenCalledTimes(2)
    expect(onToggleTag.mock.calls[0][0]).toBe("fastapi")
    expect(onToggleTag.mock.calls[1][0]).toBe("auth")
  })
})
