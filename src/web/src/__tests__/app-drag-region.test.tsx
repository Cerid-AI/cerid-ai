// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Frameless-window drag band (.app-drag-region). In a plain browser tab (no
// window.cerid) it must not exist, or it silently swallows every click on
// anything under the top of the window — including the Subjects toolbar
// (mode tabs + Search button). In the desktop shell it must still drag the
// window, so the Subjects toolbar opts out with .app-no-drag instead.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AppLayout } from "@/components/layout/app-layout"
import SubjectsPane from "@/components/subjects/subjects-pane"
import { NavigationProvider } from "@/contexts/navigation-context"
import { ChatToolbar } from "@/components/chat/chat-toolbar"
import SettingsPane from "@/components/settings/settings-pane"
import BriefsPane from "@/components/briefs/briefs-pane"
import MemoriesPane from "@/components/memories/memories-pane"
import AutomationsPane from "@/components/automations/automations-pane"
import WorkflowsPane from "@/components/workflows/workflows-pane"
import WorkflowEditor from "@/components/workflows/workflow-editor"
import * as settingsApi from "@/lib/api/settings"

vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <div data-testid="sidebar-stub" /> }))
vi.mock("@/components/layout/status-bar", () => ({ StatusBar: () => <div data-testid="status-bar-stub" /> }))
vi.mock("@/components/layout/bottom-tab-bar", () => ({ BottomTabBar: () => <div data-testid="bottom-tab-bar-stub" /> }))
vi.mock("@/components/layout/deep-link-router", () => ({ DeepLinkRouter: () => null }))
vi.mock("@/components/model-download-banner", () => ({ ModelDownloadBanner: () => null }))
vi.mock("@/hooks/use-theme", () => ({
  useTheme: () => ({ theme: "dark" as const, toggleTheme: vi.fn() }),
  applyPersistedAppearance: vi.fn(),
}))

vi.mock("@/lib/api/atlas-views", () => ({ listAtlasViews: vi.fn().mockResolvedValue([]) }))
vi.mock("@/components/subjects/atlas/Atlas", () => ({ Atlas: () => <div /> }))
vi.mock("@/components/subjects/atlas/decomposition", () => ({ DecompositionIcicle: () => <div /> }))
vi.mock("@/components/wiki/wiki-pane", () => ({ default: () => <div /> }))
vi.mock("@/components/kb/graph-explorer", () => ({ GraphExplorer: () => <div /> }))

// main.tsx's own <Toaster /> instantiation — its props are captured here
// since main.tsx has no other seam to test through (top-level createRoot
// side effect, no exported render function).
const { capturedToasterProps } = vi.hoisted(() => ({
  capturedToasterProps: { current: null as Record<string, unknown> | null },
}))
vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    message: vi.fn(),
  },
  Toaster: (props: Record<string, unknown>) => {
    capturedToasterProps.current = props
    return null
  },
}))
vi.mock("@/App", () => ({ default: () => null }))
vi.mock("@/lib/sentry", () => ({
  initSentry: vi.fn().mockResolvedValue(null),
  captureException: vi.fn(),
  addBreadcrumb: vi.fn(),
}))

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

// Chat toolbar, Settings header, and ModelDownloadBanner are the other three
// controls the B1 audit found still inside the band without an opt-out
// (Briefs/Workflows/Memories/Automations header requirements are out of this
// task's scope — see the plan's evidence note). Rather than stub
// getBoundingClientRect() per element from a hand-maintained layout table
// (jsdom returns zero rects by default, so that geometry would just replay
// the coordinates already hardcoded in index.css — it wouldn't actually
// measure anything), each opted-out root is asserted directly, matching the
// Subjects pattern above.

const noopChatToolbarProps = {
  isNarrow: false,
  showKB: false,
  onToggleKB: vi.fn(),
  autoInject: false,
  toggleAutoInject: vi.fn(),
  autoInjectThreshold: 0,
  setAutoInjectThreshold: vi.fn(),
  includePacks: false,
  toggleIncludePacks: vi.fn(),
  hallucinationEnabled: false,
  toggleHallucinationEnabled: vi.fn(),
  inlineMarkups: false,
  toggleInlineMarkups: vi.fn(),
  expertVerification: false,
  toggleExpertVerification: vi.fn(),
  onVerifyMessage: vi.fn(),
  feedbackLoop: false,
  toggleFeedbackLoop: vi.fn(),
  memoryExtraction: false,
  toggleMemoryExtraction: vi.fn(),
  showDashboard: false,
  toggleDashboard: vi.fn(),
  ragMode: "off" as const,
  setRagMode: vi.fn().mockResolvedValue(undefined),
  routingMode: "manual",
  setRoutingMode: vi.fn(),
  cycleRoutingMode: vi.fn(),
  selectedModel: "openrouter/test-model",
  onModelChange: vi.fn(),
  privateModeEnabled: false,
  privateModeLevel: 0,
  togglePrivateMode: vi.fn(),
  changePrivateModeLevel: vi.fn(),
  onNewChat: vi.fn(),
}

function renderChatToolbar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ChatToolbar {...noopChatToolbarProps} />
    </QueryClientProvider>,
  )
}

describe("Chat toolbar opts out of the drag band", () => {
  it("the toolbar root carries app-no-drag", () => {
    renderChatToolbar()
    const newChatButton = screen.getByRole("button", { name: /new chat/i })
    const toolbarRoot = newChatButton.closest(".border-b")
    expect(toolbarRoot).not.toBeNull()
    expect(toolbarRoot).toHaveClass("app-no-drag")
  })
})

function renderSettingsPane() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SettingsPane />
    </QueryClientProvider>,
  )
}

describe("Settings header opts out of the drag band", () => {
  beforeEach(() => {
    // Header renders identically in the loading/error/ready states; hanging
    // the fetch keeps the component in "loading" without needing to mock
    // the full settings payload.
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("the Settings header carries app-no-drag", () => {
    renderSettingsPane()
    const heading = screen.getByRole("heading", { name: "Settings" })
    const header = heading.closest(".border-b")
    expect(header).not.toBeNull()
    expect(header).toHaveClass("app-no-drag")
  })
})

describe("ModelDownloadBanner opts out of the drag band", () => {
  it("the idle_uncached banner root carries app-no-drag", async () => {
    // Bypass this file's own top-level `vi.mock("@/components/model-download-banner", ...)`
    // (used to keep the AppLayout drag-region tests above lightweight) to
    // exercise the real component here.
    const { ModelDownloadBanner } = await vi.importActual<
      typeof import("@/components/model-download-banner")
    >("@/components/model-download-banner")
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {}, loading: false },
      embedder: { repo: "e", cached: false, files: {}, loading: false },
    })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <ModelDownloadBanner />
      </QueryClientProvider>,
    )
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveClass("app-no-drag")
    expect(screen.getByRole("button", { name: /download now/i })).toBeInTheDocument()
  })
})

describe("Toaster clears the drag band", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>'
    capturedToasterProps.current = null
  })

  it("main.tsx renders Toaster with a 36px offset so toasts start below the 28px band", async () => {
    await act(async () => {
      await import("@/main")
    })
    expect(capturedToasterProps.current).not.toBeNull()
    expect(capturedToasterProps.current?.offset).toBe("36px")
  })
})

// Fix round: the four remaining panes flagged in the B1 report concern
// (Briefs/Memories/Automations/Workflows headers) — their action buttons sit
// in the same top-of-window position as Subjects/Settings/Chat, so they need
// the same opt-out. Each pane's header renders unconditionally except
// Workflows, whose list view gates its header behind the workflows query's
// loading state.

function renderBriefsPane() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <BriefsPane />
    </QueryClientProvider>,
  )
}

describe("Briefs header opts out of the drag band", () => {
  it("the Briefs header carries app-no-drag", () => {
    renderBriefsPane()
    const heading = screen.getByRole("heading", { name: "Briefs" })
    const header = heading.closest(".border-b")
    expect(header).not.toBeNull()
    expect(header).toHaveClass("app-no-drag")
  })
})

function renderMemoriesPane() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoriesPane />
    </QueryClientProvider>,
  )
}

describe("Memories header opts out of the drag band", () => {
  it("the Memories header carries app-no-drag", () => {
    renderMemoriesPane()
    const heading = screen.getByRole("heading", { name: "Memories" })
    const header = heading.closest(".border-b")
    expect(header).not.toBeNull()
    expect(header).toHaveClass("app-no-drag")
  })
})

function renderAutomationsPane() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <AutomationsPane />
    </QueryClientProvider>,
  )
}

describe("Automations header opts out of the drag band", () => {
  it("the Automations header carries app-no-drag", () => {
    renderAutomationsPane()
    const heading = screen.getByRole("heading", { name: "Automations" })
    const header = heading.closest(".border-b")
    expect(header).not.toBeNull()
    expect(header).toHaveClass("app-no-drag")
  })
})

describe("Workflows header opts out of the drag band", () => {
  it("the Workflows header (list view, the shared header for the pane) carries app-no-drag", async () => {
    vi.spyOn(settingsApi, "fetchWorkflows").mockResolvedValue({ workflows: [], total: 0 })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <WorkflowsPane />
      </QueryClientProvider>,
    )
    // WorkflowList (the pane's default view) renders a loading skeleton
    // until its query resolves — the header only mounts after that.
    const heading = await screen.findByRole("heading", { name: /workflows/i })
    const header = heading.closest(".border-b")
    expect(header).not.toBeNull()
    expect(header).toHaveClass("app-no-drag")
  })
})

describe("Workflow editor toolbar opts out of the drag band", () => {
  it("the Back/Templates/Add Node/Run/Save toolbar carries app-no-drag", () => {
    render(<WorkflowEditor workflow={null} onSave={() => {}} onBack={() => {}} />)
    const backButton = screen.getByRole("button", { name: /back/i })
    const toolbar = backButton.closest(".border-b")
    expect(toolbar).not.toBeNull()
    expect(toolbar).toHaveClass("app-no-drag")
    // Templates, Add Node and Save all live in the same opted-out toolbar.
    expect(screen.getByText("Templates")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add Node" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument()
  })
})
