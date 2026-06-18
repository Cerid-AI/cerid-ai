// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// F-URL-01 — URL state hygiene: switching primary nav (chat → subjects →
// sources → settings) must strip per-pane sub-tab URL params that no
// longer belong to the active pane. Without this, the URL accumulates
// stale params (`?mode=wiki&sources_mode=connectors`) that confuse
// bookmarks, deep links, and reload behaviour.

import { describe, expect, it, beforeEach, vi } from "vitest"
import { render } from "@testing-library/react"
import { useEffect } from "react"
import { clearForeignPaneParams } from "@/lib/url-state"
import { NavigationProvider, useNavigation } from "@/contexts/navigation-context"
import type { Pane } from "@/components/layout/sidebar"

beforeEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("clearForeignPaneParams", () => {
  it("strips sources_mode when switching to settings", () => {
    window.history.replaceState({}, "", "/?mode=wiki&sources_mode=connectors")
    clearForeignPaneParams("settings")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBeNull()
    expect(params.get("sources_mode")).toBeNull()
  })

  it("strips mode (subjects param) when switching to sources", () => {
    window.history.replaceState({}, "", "/?mode=wiki&entity=alex&since=2026-01-01")
    clearForeignPaneParams("sources")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBeNull()
    expect(params.get("entity")).toBeNull()
    expect(params.get("since")).toBeNull()
  })

  it("strips sources_mode when switching to subjects", () => {
    window.history.replaceState({}, "", "/?sources_mode=connectors")
    clearForeignPaneParams("subjects")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("sources_mode")).toBeNull()
  })

  it("preserves subjects params when switching to subjects", () => {
    window.history.replaceState({}, "", "/?mode=wiki&entity=alex&since=2026-01-01")
    clearForeignPaneParams("subjects")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBe("wiki")
    expect(params.get("entity")).toBe("alex")
    expect(params.get("since")).toBe("2026-01-01")
  })

  it("preserves sources params when switching to sources", () => {
    window.history.replaceState({}, "", "/?sources_mode=connectors")
    clearForeignPaneParams("sources")
    expect(new URLSearchParams(window.location.search).get("sources_mode")).toBe("connectors")
  })

  it("strips diagnostics_tab when switching to chat", () => {
    window.history.replaceState({}, "", "/?diagnostics_tab=status&mode=wiki")
    clearForeignPaneParams("chat")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("diagnostics_tab")).toBeNull()
    expect(params.get("mode")).toBeNull()
  })

  it("preserves diagnostics_tab when switching to settings", () => {
    window.history.replaceState({}, "", "/?diagnostics_tab=status")
    clearForeignPaneParams("settings")
    expect(new URLSearchParams(window.location.search).get("diagnostics_tab")).toBe("status")
  })

  it("leaves unrelated params alone", () => {
    window.history.replaceState({}, "", "/?utm_source=x&sources_mode=connectors")
    clearForeignPaneParams("settings")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("utm_source")).toBe("x")
    expect(params.get("sources_mode")).toBeNull()
  })

  it("does not add a history entry (uses replaceState)", () => {
    const before = window.history.length
    window.history.replaceState({}, "", "/?sources_mode=connectors")
    clearForeignPaneParams("settings")
    expect(window.history.length).toBe(before)
  })

  it("scenario from bug report: subjects → sources → settings leaves URL clean", () => {
    // User visits Subjects/Wiki
    window.history.replaceState({}, "", "/?mode=wiki")
    // Then Sources/Connectors — Sources mounts and writes its own param;
    // the stale ?mode= would have leaked under the old behaviour.
    clearForeignPaneParams("sources")
    window.history.replaceState({}, "", `${window.location.pathname}?sources_mode=connectors`)
    // Then Settings — pane change should strip sources_mode too.
    clearForeignPaneParams("settings")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBeNull()
    expect(params.get("sources_mode")).toBeNull()
  })
})

// Integration: mirrors AppLayout's central pane-change pipeline
// (handlePaneChange → setActivePane + clearForeignPaneParams) and
// verifies the URL stays clean across NavigationProvider goTo() calls,
// the exact pathway every cross-pane link in the app travels.
function PaneChangeHarness({ onMount }: { onMount: (goTo: (p: Pane) => void) => void }) {
  const { goTo } = useNavigation()
  useEffect(() => { onMount(goTo) }, [goTo, onMount])
  return <div>harness</div>
}

describe("AppLayout pane-change pipeline (integration)", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/")
  })

  it("NavigationProvider.goTo → handlePaneChange strips foreign params", () => {
    // Simulate AppLayout's wrapper: onPaneChange runs clearForeignPaneParams
    // before delegating to the underlying state setter.
    const setActive = vi.fn()
    const handlePaneChange = (next: Pane) => {
      clearForeignPaneParams(next)
      setActive(next)
    }

    // Seed URL with stale subjects + sources params (the bug scenario).
    window.history.replaceState({}, "", "/?mode=wiki&sources_mode=connectors")

    let captured: ((p: Pane) => void) | null = null
    render(
      <NavigationProvider activePane="subjects" onPaneChange={handlePaneChange}>
        <PaneChangeHarness onMount={(g) => { captured = g }} />
      </NavigationProvider>,
    )

    // Switch to settings via the same goTo path the rest of the app uses.
    captured!("settings")

    expect(setActive).toHaveBeenCalledWith("settings")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("mode")).toBeNull()
    expect(params.get("sources_mode")).toBeNull()
  })

  it("legacy redirect (wiki → subjects) keeps the mode param the redirect just wrote", () => {
    const setActive = vi.fn()
    const handlePaneChange = (next: Pane) => {
      clearForeignPaneParams(next)
      setActive(next)
    }

    let captured: ((p: Pane) => void) | null = null
    render(
      <NavigationProvider activePane="chat" onPaneChange={handlePaneChange}>
        <PaneChangeHarness onMount={(g) => { captured = g }} />
      </NavigationProvider>,
    )

    // goTo("wiki") → applyRedirect sets ?mode=wiki, then onPaneChange("subjects").
    // handlePaneChange must NOT strip "mode" because subjects owns it.
    captured!("wiki")

    expect(setActive).toHaveBeenCalledWith("subjects")
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("wiki")
  })

})
