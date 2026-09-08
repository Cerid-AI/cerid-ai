// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Frameless-window drag band (.app-drag-region). In a plain browser tab (no
// window.cerid) it must not exist, or it silently swallows every click on
// anything under the top of the window — including the Subjects toolbar
// (mode tabs + Search button). In the desktop shell it must still drag the
// window, so the Subjects toolbar opts out with .app-no-drag instead.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AppLayout } from "@/components/layout/app-layout"
import SubjectsPane from "@/components/subjects/subjects-pane"
import { NavigationProvider } from "@/contexts/navigation-context"

vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <div data-testid="sidebar-stub" /> }))
vi.mock("@/components/layout/status-bar", () => ({ StatusBar: () => <div data-testid="status-bar-stub" /> }))
vi.mock("@/components/layout/bottom-tab-bar", () => ({ BottomTabBar: () => <div data-testid="bottom-tab-bar-stub" /> }))
vi.mock("@/components/layout/deep-link-router", () => ({ DeepLinkRouter: () => null }))
vi.mock("@/components/model-download-banner", () => ({ ModelDownloadBanner: () => null }))
vi.mock("@/hooks/use-theme", () => ({ useTheme: () => ({ theme: "dark" as const, toggleTheme: vi.fn() }) }))

vi.mock("@/lib/api/atlas-views", () => ({ listAtlasViews: vi.fn().mockResolvedValue([]) }))
vi.mock("@/components/subjects/atlas/Atlas", () => ({ Atlas: () => <div /> }))
vi.mock("@/components/subjects/atlas/decomposition", () => ({ DecompositionIcicle: () => <div /> }))
vi.mock("@/components/wiki/wiki-pane", () => ({ default: () => <div /> }))
vi.mock("@/components/kb/graph-explorer", () => ({ GraphExplorer: () => <div /> }))

function stubMatchMedia() {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  })
}

function renderAppLayout() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <AppLayout>{() => <div data-testid="pane-content" />}</AppLayout>
    </QueryClientProvider>,
  )
}

function renderSubjects() {
  window.history.replaceState({}, "", "/")
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <NavigationProvider activePane="subjects" onPaneChange={() => {}}>
        <SubjectsPane />
      </NavigationProvider>
    </QueryClientProvider>,
  )
}

describe("frameless-window drag band", () => {
  const original = (window as unknown as { cerid?: unknown }).cerid

  beforeEach(() => {
    stubMatchMedia()
  })

  afterEach(() => {
    ;(window as unknown as { cerid?: unknown }).cerid = original
  })

  it("does not render without the desktop bridge", () => {
    delete (window as unknown as { cerid?: unknown }).cerid
    const { container } = renderAppLayout()
    expect(container.querySelector(".app-drag-region")).toBeNull()
  })

  it("renders — draggable — with the desktop bridge present", () => {
    ;(window as unknown as { cerid?: unknown }).cerid = {}
    const { container } = renderAppLayout()
    const dragRegion = container.querySelector(".app-drag-region")
    expect(dragRegion).not.toBeNull()
  })
})

describe("Subjects toolbar opts out of the drag band", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/")
  })

  it("the mode-switcher header carries app-no-drag", () => {
    renderSubjects()
    const tablist = screen.getByRole("tablist", { name: /subjects view mode/i })
    const header = tablist.parentElement
    expect(header).not.toBeNull()
    expect(header).toHaveClass("app-no-drag")
    // Both the tabs and the Search button live inside the same opted-out
    // container, so a click on either reaches the real control.
    expect(screen.getByRole("tab", { name: /atlas/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /search subjects/i })).toBeInTheDocument()
  })
})
