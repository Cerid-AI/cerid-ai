// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * App first-run gating.
 *
 * RA-12 (2026-08-11 reachability audit): the OnboardingDialog tour and its
 * `onboarding_complete` flag routing were deleted. The setup wizard is gated
 * on `/setup/status.setup_required` alone — a configured instance never
 * re-enters the wizard from a fresh browser (the beta-triage P0-B4 clobber
 * class), and per-machine first-run is DesktopSetup's job, independent of
 * any server flag.
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
  mockFetchSetupStatus.mockResolvedValue({ ...CONFIGURED_STATUS })
})

describe("App — setup-required gate (RA-12: onboarding flag routing deleted)", () => {
  it("skips the wizard on a configured instance, even in a fresh browser", async () => {
    renderApp()

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
  })

  it("ignores a server-side onboarding_complete=false — the tour flag no longer routes to the wizard", async () => {
    mockFetchSetupStatus.mockResolvedValue({ ...CONFIGURED_STATUS, onboarding_complete: false })

    renderApp()

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
  })

  it("ignores a stale localStorage onboarding cache", async () => {
    localStorage.setItem("cerid-onboarding-complete", "true")

    renderApp()

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
  })

  it("forces the wizard (non-skippable) when setup is required", async () => {
    mockFetchSetupStatus.mockResolvedValue({
      configured: false,
      setup_required: true,
      missing_keys: ["OPENROUTER_API_KEY"],
      optional_keys: [],
      configured_providers: [],
    })

    renderApp()

    const wizard = await screen.findByTestId("setup-wizard")
    expect(wizard).toHaveAttribute("data-canskip", "false")
  })

  it("shows the main app when the backend is unreachable (no wizard dead-end)", async () => {
    mockFetchSetupStatus.mockRejectedValue(new Error("network down"))

    renderApp()

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
  })

  it("hides the wizard after onComplete fires", async () => {
    mockFetchSetupStatus.mockResolvedValue({
      configured: false,
      setup_required: true,
      missing_keys: ["OPENROUTER_API_KEY"],
      optional_keys: [],
      configured_providers: [],
    })

    renderApp()
    fireEvent.click(await screen.findByText("finish-wizard"))

    await waitFor(() => expect(screen.getByTestId("app-layout")).toBeInTheDocument())
    expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument()
  })
})
