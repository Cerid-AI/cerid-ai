// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// SF-7 — SPA cold-load routing. Cold-loading a deep URL like
// /subjects?mode=timeline used to land on Chat: the initial pane was
// hardcoded to "chat" and the pathname was never consulted (nor written),
// so direct links didn't land on the pane they name. This is the surviving
// repo defect behind the "cerid:// deep-link doesn't navigate" caveat.

import { describe, expect, it, beforeEach } from "vitest"
import { paneFromLocation, syncPanePath } from "@/lib/url-state"

beforeEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("paneFromLocation", () => {
  it("maps /subjects to the subjects pane", () => {
    expect(paneFromLocation("/subjects")).toBe("subjects")
  })

  it("maps every mounted pane path", () => {
    for (const pane of ["chat", "settings", "subjects", "sources", "briefs", "workflows", "memories", "automations"] as const) {
      expect(paneFromLocation(`/${pane}`)).toBe(pane)
    }
  })

  it("tolerates a trailing slash", () => {
    expect(paneFromLocation("/subjects/")).toBe("subjects")
  })

  it("returns null for the root path (caller falls back to chat)", () => {
    expect(paneFromLocation("/")).toBeNull()
  })

  it("returns null for unknown paths", () => {
    expect(paneFromLocation("/nonsense")).toBeNull()
    expect(paneFromLocation("/api/mcp/health")).toBeNull()
  })

  it("resolves legacy pane names to the pane that replaced them (F370)", () => {
    // The app no longer writes these as pathnames, but bookmarks, shared
    // links and browser history from before the pane consolidation still do.
    // Dropping them to null landed the user on Chat with the address bar
    // still reading /wiki; see url-state-legacy-paths.test.ts for the URL
    // rewrite that goes with each.
    expect(paneFromLocation("/wiki")).toBe("subjects")
    expect(paneFromLocation("/knowledge")).toBe("sources")
  })

  it("returns null for Electron file:// style paths", () => {
    expect(paneFromLocation("/Applications/Cerid AI.app/Contents/Resources/index.html")).toBeNull()
  })
})

describe("syncPanePath", () => {
  it("writes /subjects when the subjects pane activates", () => {
    syncPanePath("subjects")
    expect(window.location.pathname).toBe("/subjects")
  })

  it("writes / for chat (canonical home)", () => {
    window.history.replaceState({}, "", "/subjects?mode=timeline")
    syncPanePath("chat")
    expect(window.location.pathname).toBe("/")
  })

  it("preserves query params and hash", () => {
    window.history.replaceState({}, "", "/?mode=timeline#frag")
    syncPanePath("subjects")
    expect(window.location.pathname).toBe("/subjects")
    expect(window.location.search).toBe("?mode=timeline")
    expect(window.location.hash).toBe("#frag")
  })

  it("does not add a history entry (uses replaceState)", () => {
    const before = window.history.length
    syncPanePath("subjects")
    expect(window.history.length).toBe(before)
  })

  it("cold-load scenario: /subjects?mode=timeline resolves pane and keeps params", () => {
    window.history.replaceState({}, "", "/subjects?mode=timeline")
    const pane = paneFromLocation(window.location.pathname) ?? "chat"
    expect(pane).toBe("subjects")
    // AppLayout then runs its normal pipeline; params the pane owns survive.
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("timeline")
  })
})
