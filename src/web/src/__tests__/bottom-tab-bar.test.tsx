// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Task 3.7 — mobile bottom tab bar (Chat / Capture / Menu, <md only).

import { describe, expect, it, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import { NavigationProvider } from "@/contexts/navigation-context"
import { BottomTabBar } from "@/components/layout/bottom-tab-bar"
import type { Pane } from "@/components/layout/sidebar"

function renderBar(activePane: Pane = "chat", onOpenMenu = vi.fn(), onPaneChange = vi.fn()) {
  return {
    onOpenMenu,
    onPaneChange,
    ...render(
      <NavigationProvider activePane={activePane} onPaneChange={onPaneChange}>
        <BottomTabBar onOpenMenu={onOpenMenu} />
      </NavigationProvider>,
    ),
  }
}

describe("BottomTabBar", () => {
  it("renders exactly Chat, Capture, and Menu with accessible names", () => {
    renderBar()
    expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Capture" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Menu" })).toBeInTheDocument()
  })

  it("is a labelled <nav> landmark", () => {
    renderBar()
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument()
  })

  it("is md:hidden (mobile only)", () => {
    renderBar()
    expect(screen.getByRole("navigation", { name: "Primary" })).toHaveClass("md:hidden")
  })

  it("clicking Chat calls goTo('chat') (onPaneChange)", () => {
    const { onPaneChange } = renderBar("settings")
    fireEvent.click(screen.getByRole("button", { name: "Chat" }))
    expect(onPaneChange).toHaveBeenCalledWith("chat")
  })

  it("marks Chat as the active tab via aria-current", () => {
    renderBar("chat")
    expect(screen.getByRole("button", { name: "Chat" })).toHaveAttribute("aria-current", "page")
  })

  it("does not mark Chat active when another pane is active", () => {
    renderBar("settings")
    expect(screen.getByRole("button", { name: "Chat" })).not.toHaveAttribute("aria-current")
  })

  it("clicking Capture dispatches the cerid:quick-capture event", () => {
    const handler = vi.fn()
    window.addEventListener("cerid:quick-capture", handler)
    renderBar()
    fireEvent.click(screen.getByRole("button", { name: "Capture" }))
    expect(handler).toHaveBeenCalledTimes(1)
    window.removeEventListener("cerid:quick-capture", handler)
  })

  it("clicking Menu invokes the sidebar-open handler", () => {
    const { onOpenMenu } = renderBar()
    fireEvent.click(screen.getByRole("button", { name: "Menu" }))
    expect(onOpenMenu).toHaveBeenCalledTimes(1)
  })

  it("is axe-clean", async () => {
    const { container } = renderBar()
    expect(await axe(container)).toHaveNoViolations()
  })

  describe("on panes where QuickCaptureFab is unmounted (BETA-001 gating)", () => {
    it.each(["sources", "subjects", "briefs"] as const)(
      "disables Capture on %s so it doesn't silently no-op",
      (pane) => {
        const handler = vi.fn()
        window.addEventListener("cerid:quick-capture", handler)
        renderBar(pane)
        const captureButton = screen.getByRole("button", { name: "Capture" })
        expect(captureButton).toBeDisabled()
        expect(captureButton).toHaveAttribute("aria-disabled", "true")
        fireEvent.click(captureButton)
        expect(handler).not.toHaveBeenCalled()
        window.removeEventListener("cerid:quick-capture", handler)
      },
    )

    it("is still axe-clean with Capture disabled", async () => {
      const { container } = renderBar("sources")
      expect(await axe(container)).toHaveNoViolations()
    })
  })

  it("keeps Capture enabled on panes where the FAB is mounted", () => {
    renderBar("settings")
    expect(screen.getByRole("button", { name: "Capture" })).not.toBeDisabled()
  })
})
