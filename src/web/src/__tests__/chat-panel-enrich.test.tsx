// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F222: the chat pane must not offer an action no server implements.
 *
 * The Enrich control POSTed to /agent/enrich, which exists on no router in
 * this repo, and the handler returned on `!res.ok` and swallowed the throw —
 * so every click on every assistant message did nothing, silently.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// jsdom polyfill — ChatPanel uses useSyncExternalStore + window.matchMedia
// for its narrow-screen responsive check. jsdom ships without matchMedia.
// ---------------------------------------------------------------------------
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
// Stub every module-level hook that ChatPanel calls.
// Stubbed before the component import so vi.mock hoisting works.
// ---------------------------------------------------------------------------

const ACTIVE_CONVERSATION = {
  id: "c1",
  title: "Conversation",
  model: "gpt-4o-mini",
  createdAt: 0,
  updatedAt: 0,
  messages: [
    { id: "m0", role: "user" as const, content: "hello", timestamp: 0 },
    { id: "m1", role: "assistant" as const, content: "hi there", timestamp: 1 },
  ],
}

vi.mock("@/contexts/conversations-context", () => ({
  useConversationsContext: () => ({
    active: ACTIVE_CONVERSATION,
    activeId: "c1",
    conversations: [],
    visibleConversations: [],
    showArchived: false,
    toggleShowArchived: vi.fn(),
    archivedCount: 0,
    setActiveId: vi.fn(),
    create: vi.fn(),
    addMessage: vi.fn(),
    updateLastMessage: vi.fn(),
    updateLastMessageModel: vi.fn(),
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    clearMessages: vi.fn(),
    remove: vi.fn(),
    rename: vi.fn(),
    archive: vi.fn(),
    unarchive: vi.fn(),
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
    setSelectedArtifactId: vi.fn(),
  }),
}))

vi.mock("@/hooks/use-orchestrated-query", () => ({
  useOrchestratedQuery: () => ({
    results: [],
    degradedReason: null,
    sourceBreakdown: null,
  }),
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
  useSmartSuggestions: () => ({ suggestions: [], clear: vi.fn() }),
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

vi.mock("@/lib/api", () => ({
  fetchSetupStatus: vi.fn().mockResolvedValue({
    configured: true,
    configured_providers: ["openai"],
    setup_complete: true,
  }),
  fetchHealthStatus: vi.fn().mockResolvedValue({
    can_verify: true,
    degradation_tier: "full",
    pipeline_providers: {},
  }),
  enableOllama: vi.fn(),
  fetchOllamaStatus: vi.fn().mockResolvedValue({ reachable: false, models: [] }),
  fetchOllamaRecommendations: vi.fn().mockResolvedValue({ recommended: "llama3.2:3b", hardware: { ram_gb: 16 }, models: [] }),
  pullOllamaModel: vi.fn().mockResolvedValue({ ok: true, body: null }),
  retestServices: vi.fn(),
  uploadFile: vi.fn(),
  MCP_BASE: "http://localhost:8888",
  mcpHeaders: () => ({}),
}))

vi.mock("@/lib/api/settings", () => ({
  fetchSetupStatus: vi.fn().mockResolvedValue({
    configured: true,
    configured_providers: ["openai"],
    setup_complete: true,
  }),
}))

// Sub-components that have their own heavy dependencies
vi.mock("@/components/chat/chat-toolbar", () => ({
  ChatToolbar: () => <div data-testid="chat-toolbar" />,
}))
// Mirrors message-bubble.tsx: the Enrich control renders on every
// non-streaming assistant message exactly when an onEnrich prop arrives.
vi.mock("@/components/chat/chat-messages", () => ({
  ChatMessages: ({ onEnrich }: { onEnrich?: (id: string, content: string) => void }) => (
    <div data-testid="chat-messages">
      {onEnrich && (
        <button
          type="button"
          aria-label="Enrich with external sources"
          onClick={() => onEnrich("m1", "hi there")}
        >
          Enrich
        </button>
      )}
    </div>
  ),
}))
vi.mock("@/components/chat/chat-input", () => ({
  ChatInput: () => <input aria-label="Chat input" data-testid="chat-input" />,
}))
vi.mock("@/components/chat/credit-banner", () => ({
  CreditBanner: () => null,
}))
vi.mock("@/components/chat/degradation-banner", () => ({
  DegradationBanner: () => null,
}))
vi.mock("@/components/chat/chat-dashboard", () => ({
  ChatDashboard: () => null,
}))
vi.mock("@/components/chat/model-switch-dialog", () => ({
  ModelSwitchDialog: () => null,
}))
vi.mock("@/components/kb/kb-context-panel", () => ({
  KBContextPanel: () => null,
}))
vi.mock("@/components/kb/knowledge-console", () => ({
  KnowledgeConsole: () => null,
}))
vi.mock("@/components/audit/hallucination-panel", () => ({
  HallucinationPanel: () => null,
}))
vi.mock("@/components/audit/verification-status-bar", () => ({
  VerificationStatusBar: () => null,
}))
vi.mock("@/components/kb/upload-dialog", () => ({
  UploadDialog: () => null,
}))
vi.mock("@/components/layout/split-pane", () => ({
  SplitPane: ({ left, right }: { left: React.ReactNode; right: React.ReactNode }) => (
    <>{left}{right}</>
  ),
}))
vi.mock("react-resizable-panels", () => ({
  Group: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Separator: () => <hr />,
  PanelSeparator: () => <hr />,
}))

// ---------------------------------------------------------------------------
// Now import the component under test
// ---------------------------------------------------------------------------

import { ChatPanel } from "@/components/chat/chat-panel"

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("ChatPanel — no dead Enrich affordance", () => {
  it("does not render an Enrich control on an assistant message", () => {
    render(<ChatPanel />, { wrapper: makeWrapper() })
    expect(screen.getByTestId("chat-messages")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /enrich with external sources/i }),
    ).not.toBeInTheDocument()
  })

  it("issues no request to /agent/enrich while the conversation renders", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")
    render(<ChatPanel />, { wrapper: makeWrapper() })
    const urls = fetchSpy.mock.calls.map((c) => String(c[0]))
    expect(urls.some((u) => u.includes("/agent/enrich"))).toBe(false)
  })
})
