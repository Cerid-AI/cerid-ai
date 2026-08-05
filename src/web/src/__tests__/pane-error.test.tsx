// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import { ServerCrash } from "lucide-react"
import { PaneError } from "@/components/ui/pane-error"

describe("PaneError — inline form (default)", () => {
  it("renders title and description", () => {
    render(
      <PaneError
        title="Failed to load communities"
        description="The backend may be unavailable. Try again."
      />,
    )
    expect(screen.getByText("Failed to load communities")).toBeInTheDocument()
    expect(screen.getByText(/backend may be unavailable/)).toBeInTheDocument()
  })

  it("renders Retry button when onRetry is provided", () => {
    const onRetry = vi.fn()
    render(<PaneError title="Error" onRetry={onRetry} />)
    const btn = screen.getByRole("button", { name: /retry/i })
    expect(btn).toBeInTheDocument()
    fireEvent.click(btn)
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it("hides Retry button when onRetry is omitted", () => {
    render(<PaneError title="Error" description="Something failed." />)
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument()
  })

  it("uses a custom icon when provided", () => {
    render(<PaneError title="Server error" icon={ServerCrash} />)
    expect(screen.getByText("Server error")).toBeInTheDocument()
  })

  it("has role=alert on the container", () => {
    const { container } = render(<PaneError title="Error" />)
    expect(container.querySelector("[role=alert]")).toBeTruthy()
  })

  it("is axe-clean (D.3) — inline without retry", async () => {
    const { container } = render(
      <PaneError title="Failed to load" description="Check the backend." />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) — inline with retry", async () => {
    const { container } = render(
      <PaneError
        title="Failed to load"
        description="Check the backend."
        onRetry={() => {}}
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("PaneError — fullPage form", () => {
  it("renders title and description in fullPage mode", () => {
    render(
      <PaneError
        title="Failed to load communities"
        description="The backend may be unavailable."
        fullPage
      />,
    )
    expect(screen.getByText("Failed to load communities")).toBeInTheDocument()
    expect(screen.getByText(/backend may be unavailable/)).toBeInTheDocument()
  })

  it("renders Retry button in fullPage mode when onRetry provided", () => {
    const onRetry = vi.fn()
    render(<PaneError title="Error" onRetry={onRetry} fullPage />)
    const btn = screen.getByRole("button", { name: /retry/i })
    expect(btn).toBeInTheDocument()
    fireEvent.click(btn)
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it("centres content — wrapper has flex items-center justify-center", () => {
    const { container } = render(<PaneError title="Error" fullPage />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toMatch(/flex/)
    expect(wrapper.className).toMatch(/items-center/)
    expect(wrapper.className).toMatch(/justify-center/)
  })

  it("is axe-clean (D.3) — fullPage with retry", async () => {
    const { container } = render(
      <PaneError
        title="Failed to load"
        description="Check the backend."
        onRetry={() => {}}
        fullPage
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
