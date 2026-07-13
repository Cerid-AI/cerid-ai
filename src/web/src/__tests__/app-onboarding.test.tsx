// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * App first-run gating (beta triage 2026-07-12 P0-B4).
 *
 * The wizard used to be gated on localStorage alone, so any fresh browser
 * re-entered onboarding on a configured instance. The backend's
 * `/setup/status.onboarding_complete` is now the source of truth;
 * localStorage remains only a cache (and the fallback for older backends
 * that omit the field).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mocks — App composes the entire shell; stub everything except the gating.
// ---------------------------------------------------------------------------

const mockFetchSetupStatus = vi.fn()
const mockFetchSettings = vi.fn()

vi.mock("@/lib/api", () => ({
  fetchSetupStatus: (...args: unknown[]) => mockFetchSetupStatus(...args),
  fetchSettings: (...args: unknown[]) => mockFetchSettings(...args),
  setTierOverride: vi.fn(),
}))

vi.mock("@/components/setup/setup-wizard", () => ({
  SetupWizard: ({ canSkip, onComplete }: { canSkip?: boolean; onComplete: () => void }) => (
    <div data-testid="setup-wizard" data-canskip={String(!!canSkip)}>
      <button onClick={onComplete}>finish-wizard</button>
    </div>
  ),
}))

vi.mock("@/components/layout/app-layout", () => ({
  AppLayout: () => <div data-testid="app-layout" />,
}))

// NOTE: each factory defines its own passthrough — vi.mock factories are
// hoisted above module-scope consts (TDZ crash if shared).
type Passthrough = { children?: React.ReactNode }

vi.mock("@/components/layout/app-error-boundary", () => ({ AppErrorBoundary: ({ children }: Passthrough) => <>{children}</> }))
vi.mock("@/components/ui/pane-error-boundary", () => ({ PaneErrorBoundary: ({ children }: Passthrough) => <>{children}</> }))
vi.mock("@/components/chat/chat-panel", () => ({ ChatPanel: () => null }))
vi.mock("@/components/quick-capture/quick-capture-fab", () => ({ QuickCaptureFab: () => null }))
vi.mock("@/contexts/kb-injection-context", () => ({ KBInjectionProvider: ({ children }: Passthrough) => <>{children}</> }))
vi.mock("@/contexts/conversations-context", () => ({ ConversationsProvider: ({ children }: Passthrough) => <>{children}</> }))
vi.mock("@/contexts/auth-context", () => ({ AuthProvider: ({ children }: Passthrough) => <>{children}</> }))
vi.mock("@/components/ui/tooltip", () => ({ TooltipProvider: ({ children }: Passthrough) => <>{children}</> }))
vi.mock("@/components/auth/protected-route", () => ({ ProtectedRoute: ({ children }: Passthrough) => <>{children}</> }))
vi.mock("@/hooks/use-theme", () => ({ useTheme: () => {} }))
vi.mock("@/components/ui/liquid-glass-defs", () => ({ LiquidGlassDefs: () => null }))
vi.mock("@/components/ui/opening-sequence", () => ({ OpeningSequence: () => null }))

import App from "@/App"

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

const CONFIGURED_STATUS = {
  configured: true,
  setup_required: false,
  missing_keys: [],
  optional_keys: [],
  configured_providers: ["openrouter"],
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  mockFetchSettings.mockResolvedValue({ multi_user: false, feature_tier: "community" })
  mockFetchSetupStatus.mockResolvedValue({ ...CONFIGURED_STATUS, onboarding_complete: true })
})

describe("App — server-side onboarding gate", () => {
  it("skips the wizard in a fresh browser when the backend says onboarding is complete", async () => {
    // localStorage empty — pre-fix this re-entered the wizard.
    renderApp()

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
    // Flag is cached locally for offline/legacy sessions
    expect(localStorage.getItem("cerid-onboarding-complete")).toBe("true")
  })

  it("shows the skippable wizard when the backend says onboarding is incomplete, overriding the localStorage cache", async () => {
    localStorage.setItem("cerid-onboarding-complete", "true")
    mockFetchSetupStatus.mockResolvedValue({ ...CONFIGURED_STATUS, onboarding_complete: false })

    renderApp()

    const wizard = await screen.findByTestId("setup-wizard")
    expect(wizard).toHaveAttribute("data-canskip", "true")
    // Stale cache is dropped — the backend is the source of truth
    expect(localStorage.getItem("cerid-onboarding-complete")).toBeNull()
  })

  it("falls back to the localStorage cache when an older backend omits the flag", async () => {
    localStorage.setItem("cerid-onboarding-complete", "true")
    mockFetchSetupStatus.mockResolvedValue({ ...CONFIGURED_STATUS })

    renderApp()

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
  })

  it("shows the wizard on an older backend with no localStorage flag (legacy first run)", async () => {
    mockFetchSetupStatus.mockResolvedValue({ ...CONFIGURED_STATUS })

    renderApp()

    expect(await screen.findByTestId("setup-wizard")).toBeInTheDocument()
  })

  it("forces the wizard (non-skippable) when setup is required", async () => {
    mockFetchSetupStatus.mockResolvedValue({
      configured: false,
      setup_required: true,
      missing_keys: ["OPENROUTER_API_KEY"],
      optional_keys: [],
      configured_providers: [],
      onboarding_complete: false,
    })

    renderApp()

    const wizard = await screen.findByTestId("setup-wizard")
    expect(wizard).toHaveAttribute("data-canskip", "false")
  })

  it("hides the wizard after onComplete fires", async () => {
    mockFetchSetupStatus.mockResolvedValue({ ...CONFIGURED_STATUS, onboarding_complete: false })

    renderApp()
    fireEvent.click(await screen.findByText("finish-wizard"))

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
  })
})
