// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Phase A Day 9 — Subjects consolidation: legacy pane targets
// (wiki, communities, memories) are transparently redirected to
// the new Subjects pane with the right mode written to ?mode=.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { useEffect } from "react"
import { NavigationProvider, useNavigation, type NavigationOptions } from "@/contexts/navigation-context"
import type { Pane } from "@/components/layout/sidebar"

function Caller({ target, options }: { target: Pane; options?: NavigationOptions }) {
  const { goTo } = useNavigation()
  useEffect(() => {
    goTo(target, options)
  }, [goTo, target, options])
  return <div>caller</div>
}

beforeEach(() => {
  // Reset URL between tests
  window.history.replaceState({}, "", "/")
})

describe("NavigationProvider — legacy redirects", () => {
  it("routes goTo('wiki') to subjects pane and sets ?mode=wiki", () => {
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="wiki" />
      </NavigationProvider>,
    )
    expect(screen.getByText("caller")).toBeInTheDocument()
    expect(onPaneChange).toHaveBeenCalledWith("subjects")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBe("wiki")
  })

  it("routes goTo('communities') to subjects pane and sets ?mode=atlas", () => {
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="communities" />
      </NavigationProvider>,
    )
    expect(onPaneChange).toHaveBeenCalledWith("subjects")
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("atlas")
  })

  it("routes goTo('memories') to subjects pane and sets ?mode=atlas", () => {
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="memories" />
      </NavigationProvider>,
    )
    expect(onPaneChange).toHaveBeenCalledWith("subjects")
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("atlas")
  })

  it("routes goTo('knowledge') to sources pane and sets ?sources_mode=library", () => {
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="knowledge" />
      </NavigationProvider>,
    )
    expect(onPaneChange).toHaveBeenCalledWith("sources")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("sources_mode")).toBe("library")
    // Subjects ?mode= must NOT leak across the redirect
    expect(params.get("mode")).toBeNull()
  })

  it("passes through non-redirected panes unchanged", () => {
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="settings" />
      </NavigationProvider>,
    )
    expect(onPaneChange).toHaveBeenCalledWith("settings")
    // No mode param leaked
    expect(new URLSearchParams(window.location.search).get("mode")).toBeNull()
  })

  it("preserves existing ?entity= when adding ?mode=", () => {
    window.history.replaceState({}, "", "/?entity=alex")
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="wiki" />
      </NavigationProvider>,
    )
    const params = new URLSearchParams(window.location.search)
    expect(params.get("entity")).toBe("alex")
    expect(params.get("mode")).toBe("wiki")
  })
})

describe("NavigationProvider — goTo options", () => {
  it("writes ?mode= and ?entity= from options", () => {
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="subjects" options={{ mode: "atlas", entity: "alex" }} />
      </NavigationProvider>,
    )
    expect(onPaneChange).toHaveBeenCalledWith("subjects")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBe("atlas")
    expect(params.get("entity")).toBe("alex")
  })

  it("options override redirect-map defaults when both present", () => {
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        {/* Legacy 'wiki' redirect maps to mode=wiki, but options override to atlas */}
        <Caller target="wiki" options={{ mode: "atlas", entity: "x" }} />
      </NavigationProvider>,
    )
    expect(onPaneChange).toHaveBeenCalledWith("subjects")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBe("atlas")
  })

  it("clears ?entity= when entity option is empty string", () => {
    window.history.replaceState({}, "", "/?entity=alex")
    const onPaneChange = vi.fn()
    render(
      <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
        <Caller target="subjects" options={{ entity: "" }} />
      </NavigationProvider>,
    )
    expect(new URLSearchParams(window.location.search).get("entity")).toBeNull()
  })
})
