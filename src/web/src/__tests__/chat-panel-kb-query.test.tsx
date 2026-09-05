// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Slice 3: chat in smart (default) RAG mode must fire exactly one
 * POST /agent/query per turn — useOrchestratedQuery, not the sibling
 * useKBContext auto-query. chat-panel.test.tsx mocks both hooks, so this
 * file spies the network helpers instead.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"

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

const emptyEnvelope = {
  results: [],
  confidence: 0,
  total_results: 0,
  execution_time_ms: 0,
  context: "",
  sources: [],
  domains_searched: [],
}

const apiMocks = vi.hoisted(() => ({
  queryKB: vi.fn(),
  queryKBOrchestrated: vi.fn(),
  fetchSetupStatus: vi.fn(),
  fetchHealthStatus: vi.fn(),
}))

const settingsState = vi.hoisted(() => ({ ragMode: "smart" as string }))

const LONG_USER_MESSAGE = "how does auth work"

const activeConversation = {
  id: "c1",
  title: "Test",
  messages: [
    { id: "m1", role: "user" as const, content: LONG_USER_MESSAGE, timestamp: 1 },
    { id: "m2", role: "assistant" as const, content: "hello", timestamp: 2 },
  ],
  model: "openrouter/anthropic/claude-sonnet-4.6",
  createdAt: 0,
  updatedAt: 0,
}

vi.mock("@/lib/api", () => ({
  queryKB: apiMocks.queryKB,
  queryKBOrchestrated: apiMocks.queryKBOrchestrated,
  fetchSetupStatus: apiMocks.fetchSetupStatus,
  fetchHealthStatus: apiMocks.fetchHealthStatus,
  enableOllama: vi.fn(),
  fetchOllamaStatus: vi.fn().mockResolvedValue({ reachable: false, models: [] }),
  fetchOllamaRecommendations: vi.fn(),
  pullOllamaModel: vi.fn(),
  retestServices: vi.fn().mockResolvedValue({}),
  uploadFile: vi.fn(),
  MCP_BASE: "http://localhost:8888",
  mcpHeaders: () => ({}),
}))

vi.mock("@/lib/api/settings", () => ({
  fetchSetupStatus: apiMocks.fetchSetupStatus,
}))

vi.mock("@/lib/api/routing", () => ({
  fetchRoutingInfo: vi.fn().mockResolvedValue({ model_registry: undefined }),
}))

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
    mergeCompressedHistory: vi.fn(),
    clearMessages: vi.fn(),
  }),
}))

vi.mock("@/hooks/use-settings", () => ({
  useSettings: () => ({
    feedbackLoop: false, toggleFeedbackLoop: vi.fn(),
    showDashboard: false, toggleDashboard: vi.fn(),
    ragMode: settingsState.ragMode, setRagMode: vi.fn(),
    routingMode: "auto", setRoutingMode: vi.fn(), cycleRoutingMode: vi.fn(),
    autoInject: false, toggleAutoInject: vi.fn(),
    autoInjectThreshold: 0.8, setAutoInjectThreshold: vi.fn(),
    autoInjectMax: 3,
    includePacks: true, toggleIncludePacks: vi.fn(),
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
vi.mock("@/hooks/use-context-sources", () => ({
  useContextSources: () => ({
    sources: { kb: true, memory: true, external: true },
    toggleSource: vi.fn(),
  }),
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
    verificationStatusForMsg: () => null,
    verificationRecBanner: null,
    setVerificationRecBanner: vi.fn(),
    handleVerifyMessage: vi.fn(),
    selectedVerificationMsgId: null,
    setSelectedVerificationMsgId: vi.fn(),
    allVerificationReports: {},
    claimUpdates: {},
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
  PanelSeparator: () => <hr />,
}))

import { ChatPanel } from "@/components/chat/chat-panel"

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <KBInjectionProvider>{children}</KBInjectionProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  settingsState.ragMode = "smart"
  apiMocks.queryKB.mockReset().mockResolvedValue(emptyEnvelope)
  apiMocks.queryKBOrchestrated.mockReset().mockResolvedValue(emptyEnvelope)
  apiMocks.fetchSetupStatus.mockReset().mockResolvedValue({
    configured: true,
    configured_providers: ["openai"],
    setup_complete: true,
  })
  apiMocks.fetchHealthStatus.mockReset().mockResolvedValue({
    can_verify: true,
    degradation_tier: "full",
    pipeline_providers: {},
  })
})

describe("ChatPanel — one agent query per turn (Slice 3)", () => {
  it("in smart mode does not auto-query KB when the latest user message is long", async () => {
    settingsState.ragMode = "smart"
    render(<ChatPanel />, { wrapper: makeWrapper() })
    await waitFor(() => expect(apiMocks.queryKBOrchestrated).toHaveBeenCalled())
    expect(apiMocks.queryKB).not.toHaveBeenCalled()
  })

  it("in off mode still auto-queries via useKBContext", async () => {
    settingsState.ragMode = "off"
    render(<ChatPanel />, { wrapper: makeWrapper() })
    await waitFor(() => expect(apiMocks.queryKB).toHaveBeenCalled())
    expect(apiMocks.queryKBOrchestrated).not.toHaveBeenCalled()
  })
})
