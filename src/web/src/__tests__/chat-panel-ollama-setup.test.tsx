// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Local-AI setup flow (runOllamaSetup) — P0-B beta triage.
 *
 * The "Speed up with local AI" banner's setup flow used a raw string
 * compare against the served-model list, so a Quenchforge gateway serving
 * dash aliases ("llama3.1-8b") never matched the colon-tag recommendation
 * ("llama3.1:8b"). That fired an unnecessary POST /ollama/pull, which
 * Quenchforge answers with {"status":"not_implemented"} — rendered as
 * "Setup failed" even though a perfectly good chat model was being served.
 *
 * These tests drive the real ChatPanel flow: alias-matched models must not
 * pull; an unsupported pull must fall back to the served default; only a
 * backend serving nothing may fail, and then with the backend's hint.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

if (typeof window.matchMedia !== "function") {
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

// ---------------------------------------------------------------------------
// Hoisted API mocks — reconfigured per test.
// ---------------------------------------------------------------------------

const apiMocks = vi.hoisted(() => ({
  fetchOllamaStatus: vi.fn(),
  fetchOllamaRecommendations: vi.fn(),
  pullOllamaModel: vi.fn(),
  enableOllama: vi.fn(),
  fetchHealthStatus: vi.fn(),
  fetchSetupStatus: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  fetchSetupStatus: apiMocks.fetchSetupStatus,
  fetchHealthStatus: apiMocks.fetchHealthStatus,
  enableOllama: apiMocks.enableOllama,
  fetchOllamaStatus: apiMocks.fetchOllamaStatus,
  fetchOllamaRecommendations: apiMocks.fetchOllamaRecommendations,
  pullOllamaModel: apiMocks.pullOllamaModel,
  retestServices: vi.fn().mockResolvedValue({}),
  uploadFile: vi.fn(),
  MCP_BASE: "http://localhost:8888",
  mcpHeaders: () => ({}),
}))

vi.mock("@/lib/api/settings", () => ({
  fetchSetupStatus: apiMocks.fetchSetupStatus,
}))

// ---------------------------------------------------------------------------
// Hook + subcomponent stubs (same shape as chat-panel.test.tsx, but with an
// active conversation of 3 messages so the local-AI banner renders).
// ---------------------------------------------------------------------------

const activeConversation = {
  id: "c1",
  title: "Test",
  messages: [
    { id: "m1", role: "user" as const, content: "hi", timestamp: 1 },
    { id: "m2", role: "assistant" as const, content: "hello", timestamp: 2 },
    { id: "m3", role: "user" as const, content: "more", timestamp: 3 },
  ],
  model: "openrouter/anthropic/claude-sonnet-4.6",
  createdAt: 0,
  updatedAt: 0,
}

vi.mock("@/contexts/conversations-context", () => ({
  useConversationsContext: () => ({
    active: activeConversation,
    activeId: "c1",
    conversations: [activeConversation],
    setActiveId: vi.fn(),
    create: vi.fn(),
    addMessage: vi.fn(),
    updateLastMessage: vi.fn(),
    updateLastMessageModel: vi.fn(),
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    clearMessages: vi.fn(),
  }),
}))

vi.mock("@/hooks/use-settings", () => ({
  useSettings: () => ({
    feedbackLoop: false, toggleFeedbackLoop: vi.fn(),
    showDashboard: false, toggleDashboard: vi.fn(),
    ragMode: "smart", setRagMode: vi.fn(),
    routingMode: "auto", setRoutingMode: vi.fn(), cycleRoutingMode: vi.fn(),
    autoInject: false, toggleAutoInject: vi.fn(),
    autoInjectThreshold: 0.8, setAutoInjectThreshold: vi.fn(),
    costSensitivity: "medium",
    hallucinationEnabled: false, toggleHallucinationEnabled: vi.fn(),
    memoryExtraction: false, toggleMemoryExtraction: vi.fn(),
    inlineMarkups: false, toggleInlineMarkups: vi.fn(),
    expertVerification: false, toggleExpertVerification: vi.fn(),
    privateModeEnabled: false, privateModeLevel: "none",
    togglePrivateMode: vi.fn(), changePrivateModeLevel: vi.fn(),
  }),
}))

vi.mock("@/hooks/use-chat", () => ({
  useChat: () => ({ send: vi.fn(), stop: vi.fn(), isStreaming: false }),
}))
vi.mock("@/hooks/use-chat-send", () => ({
  useChatSend: () => ({
    autoRouteNotice: null,
    lastAutoInjectCount: 0,
    resetAutoInjectCount: vi.fn(),
    handleSend: vi.fn(),
  }),
}))
vi.mock("@/hooks/use-kb-context", () => ({
  useKBContext: () => ({
    results: [],
    injectedContext: [],
    clearInjected: vi.fn(),
    injectResult: vi.fn(),
    setSelectedArtifactId: vi.fn(),
  }),
}))
vi.mock("@/hooks/use-orchestrated-query", () => ({
  useOrchestratedQuery: () => ({ results: [], degradedReason: null, sourceBreakdown: null }),
}))
vi.mock("@/hooks/use-context-sources", () => ({
  useContextSources: () => ({ sources: [], toggleSource: vi.fn() }),
}))
vi.mock("@/hooks/use-model-router", () => ({
  useModelRouter: () => ({ recommendation: null, dismiss: vi.fn(), resetDismiss: vi.fn() }),
}))
vi.mock("@/hooks/use-model-switch", () => ({
  useModelSwitch: () => ({
    pendingSwitch: null,
    isSummarizing: false,
    initSwitch: vi.fn(),
    executeSwitch: vi.fn(),
    cancelSwitch: vi.fn(),
  }),
}))
vi.mock("@/hooks/use-smart-suggestions", () => ({
  useSmartSuggestions: () => ({
    suggestions: [],
    clear: vi.fn(),
    debouncedSearch: vi.fn(),
    dismissSuggestion: vi.fn(),
  }),
}))
vi.mock("@/hooks/use-verification-orchestrator", () => ({
  useVerificationOrchestrator: () => ({
    halReport: null,
    halLoading: false,
    verification: { phase: "idle" },
    verificationStatusForMsg: null,
    verificationRecBanner: null,
    setVerificationRecBanner: vi.fn(),
    handleVerifyMessage: vi.fn(),
    selectedVerificationMsgId: null,
    setSelectedVerificationMsgId: vi.fn(),
    allVerificationReports: {},
    claimUpdates: new Map(),
    expertVerifiedClaims: new Set(),
    handleClaimUpdate: vi.fn(),
  }),
}))

vi.mock("@/components/chat/chat-toolbar", () => ({ ChatToolbar: () => <div data-testid="chat-toolbar" /> }))
vi.mock("@/components/chat/chat-messages", () => ({ ChatMessages: () => <div data-testid="chat-messages" /> }))
vi.mock("@/components/chat/chat-input", () => ({ ChatInput: () => <input aria-label="Chat input" /> }))
vi.mock("@/components/chat/credit-banner", () => ({ CreditBanner: () => null }))
vi.mock("@/components/chat/degradation-banner", () => ({ DegradationBanner: () => null }))
vi.mock("@/components/chat/chat-dashboard", () => ({ ChatDashboard: () => null }))
vi.mock("@/components/chat/model-switch-dialog", () => ({ ModelSwitchDialog: () => null }))
vi.mock("@/components/kb/kb-context-panel", () => ({ KBContextPanel: () => null }))
vi.mock("@/components/kb/knowledge-console", () => ({ KnowledgeConsole: () => null }))
vi.mock("@/components/audit/hallucination-panel", () => ({ HallucinationPanel: () => null }))
vi.mock("@/components/audit/verification-status-bar", () => ({ VerificationStatusBar: () => null }))
vi.mock("@/components/kb/upload-dialog", () => ({ UploadDialog: () => null }))
vi.mock("@/components/layout/split-pane", () => ({
  SplitPane: ({ left, right }: { left?: React.ReactNode; right?: React.ReactNode }) => (
    <>
      <div>{left}</div>
      <div>{right}</div>
    </>
  ),
}))
vi.mock("react-resizable-panels", () => ({
  Group: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Separator: () => <hr />,
}))

import { ChatPanel } from "@/components/chat/chat-panel"

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function notImplementedError(): Error & { code?: string; hint?: string } {
  const err: Error & { code?: string; hint?: string } = new Error(
    "Quenchforge does not support model pull from this UI.",
  )
  err.code = "model_pull_unsupported"
  err.hint = "Run `quenchforge migrate-from-ollama` to symlink your existing Ollama models."
  return err
}

async function startSetup() {
  const user = userEvent.setup()
  render(<ChatPanel />, { wrapper: makeWrapper() })
  await user.click(await screen.findByRole("button", { name: /enable/i }))
}

beforeEach(() => {
  localStorage.clear()
  for (const mock of Object.values(apiMocks)) mock.mockReset()
  apiMocks.fetchSetupStatus.mockResolvedValue({
    configured: true,
    configured_providers: ["openrouter"],
    setup_complete: true,
  })
  // No local pipeline stages yet — makes the local-AI banner render.
  apiMocks.fetchHealthStatus.mockResolvedValue({
    can_verify: true,
    degradation_tier: "full",
    pipeline_providers: {},
  })
  apiMocks.enableOllama.mockResolvedValue({ status: "ok", provider: "ollama", model: "", url: "" })
})

describe("runOllamaSetup — alias matching + pull fallback (P0-B)", () => {
  it("served dash-alias matches colon-tag recommendation: no pull, setup succeeds", async () => {
    apiMocks.fetchOllamaStatus.mockResolvedValue({
      enabled: false,
      url: "http://localhost:11434",
      reachable: true,
      models: ["llama3.1-8b", "nomic-embed-text-v1.5"],
      default_model: "llama3.1-8b",
      default_model_installed: true,
    })
    apiMocks.fetchOllamaRecommendations.mockResolvedValue({
      recommended: "llama3.1:8b",
      hardware: { ram_gb: 128, cpu: "", gpu: "", platform: "" },
      models: [],
    })

    await startSetup()

    expect(await screen.findByText(/Setup complete/i)).toBeInTheDocument()
    expect(apiMocks.pullOllamaModel).not.toHaveBeenCalled()
    // Enabled under the alias the gateway actually serves
    expect(apiMocks.enableOllama).toHaveBeenCalledWith("llama3.1-8b")
    expect(screen.queryByText(/Setup failed/i)).not.toBeInTheDocument()
  })

  it("pull not_implemented falls back to the served default instead of failing", async () => {
    apiMocks.fetchOllamaStatus.mockResolvedValue({
      enabled: false,
      url: "http://localhost:11434",
      reachable: true,
      models: ["llama3.1-8b", "nomic-embed-text-v1.5"],
      default_model: "llama3.1-8b",
      default_model_installed: true,
    })
    apiMocks.fetchOllamaRecommendations.mockResolvedValue({
      recommended: "llama3.2:3b",
      hardware: { ram_gb: 128, cpu: "", gpu: "", platform: "" },
      models: [],
    })
    apiMocks.pullOllamaModel.mockRejectedValue(notImplementedError())

    await startSetup()

    expect(await screen.findByText(/Setup complete/i)).toBeInTheDocument()
    expect(apiMocks.pullOllamaModel).toHaveBeenCalledWith("llama3.2:3b")
    expect(apiMocks.enableOllama).toHaveBeenCalledWith("llama3.1-8b")
    expect(screen.getByText(/using served model llama3\.1-8b/i)).toBeInTheDocument()
    expect(screen.queryByText(/Setup failed/i)).not.toBeInTheDocument()
  })

  it("fails only when nothing is served, surfacing the backend hint", async () => {
    apiMocks.fetchOllamaStatus.mockResolvedValue({
      enabled: false,
      url: "http://localhost:11434",
      reachable: true,
      models: [],
      default_model: "",
      default_model_installed: false,
    })
    apiMocks.fetchOllamaRecommendations.mockRejectedValue(new Error("no recs"))
    apiMocks.pullOllamaModel.mockRejectedValue(notImplementedError())

    await startSetup()

    expect(await screen.findByText(/Setup failed/i)).toBeInTheDocument()
    expect(screen.getByText(/migrate-from-ollama/i)).toBeInTheDocument()
    expect(apiMocks.enableOllama).not.toHaveBeenCalled()
  })
})
