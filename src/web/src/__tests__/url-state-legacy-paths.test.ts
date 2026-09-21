// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, beforeEach } from "vitest"
import { paneFromLocation } from "@/lib/url-state"

// F370 — the Phase A/B/C pane consolidation retired /wiki, /knowledge,
// /monitoring, /audit, /agents and /communities as pathnames. Cold loads of
// those URLs resolved to null, so App.tsx fell back to Chat while the address
// bar still read /wiki: a stale bookmark landed somewhere else with no
// explanation, and a reload repeated the failure.
beforeEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("paneFromLocation — legacy pane paths", () => {
  it("resolves /wiki to the Subjects pane in wiki mode", () => {
    window.history.replaceState({}, "", "/wiki")
    expect(paneFromLocation("/wiki")).toBe("subjects")
    expect(window.location.pathname).toBe("/subjects")
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("wiki")
  })

  it("resolves /communities to the Subjects pane in communities mode", () => {
    window.history.replaceState({}, "", "/communities")
    expect(paneFromLocation("/communities")).toBe("subjects")
    expect(window.location.pathname).toBe("/subjects")
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("communities")
  })

  it("resolves /knowledge to the Sources pane library tab", () => {
    window.history.replaceState({}, "", "/knowledge")
    expect(paneFromLocation("/knowledge")).toBe("sources")
    expect(window.location.pathname).toBe("/sources")
    expect(new URLSearchParams(window.location.search).get("sources_mode")).toBe("library")
  })

  it("resolves /monitoring to Settings → Diagnostics → Status", () => {
    window.history.replaceState({}, "", "/monitoring")
    expect(paneFromLocation("/monitoring")).toBe("settings")
    expect(window.location.pathname).toBe("/settings")
    expect(new URLSearchParams(window.location.search).get("diagnostics_tab")).toBe("status")
  })

  it("resolves /audit to the Settings analytics section", () => {
    window.history.replaceState({}, "", "/audit")
    expect(paneFromLocation("/audit")).toBe("settings")
    expect(window.location.pathname).toBe("/settings")
    expect(new URLSearchParams(window.location.search).get("category")).toBe("analytics")
  })

  it("resolves /agents to Settings → Diagnostics → Activity", () => {
    window.history.replaceState({}, "", "/agents")
    expect(paneFromLocation("/agents")).toBe("settings")
    expect(new URLSearchParams(window.location.search).get("diagnostics_tab")).toBe("activity")
  })

  it("preserves unrelated query params across the rewrite", () => {
    window.history.replaceState({}, "", "/wiki?entity=cerid&utm_source=docs")
    expect(paneFromLocation("/wiki")).toBe("subjects")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("entity")).toBe("cerid")
    expect(params.get("utm_source")).toBe("docs")
    expect(params.get("mode")).toBe("wiki")
  })

  it("is idempotent — the rewritten path resolves to the same pane", () => {
    window.history.replaceState({}, "", "/wiki")
    expect(paneFromLocation("/wiki")).toBe("subjects")
    expect(paneFromLocation(window.location.pathname)).toBe("subjects")
    expect(window.location.pathname).toBe("/subjects")
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("wiki")
  })

  it("leaves live pane paths and unknown paths alone", () => {
    expect(paneFromLocation("/subjects")).toBe("subjects")
    window.history.replaceState({}, "", "/nope")
    expect(paneFromLocation("/nope")).toBeNull()
    expect(window.location.pathname).toBe("/nope")
  })
})
