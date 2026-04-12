// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useCallback, useState, useMemo, useSyncExternalStore } from "react"
import { Group, Panel, Separator as PanelSeparator } from "react-resizable-panels"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { AlertTriangle, Check, Cpu, Copy, Database, Loader2 as Loader2Icon, X, Zap, Sparkles, MessageSquarePlus, Clock, ShieldCheck } from "lucide-react"
import { CreditBanner } from "./credit-banner"
import { DegradationBanner } from "./degradation-banner"
import { ChatToolbar } from "./chat-toolbar"
import { ChatMessages } from "./chat-messages"
import { ChatInput } from "./chat-input"
import { SplitPane } from "@/components/layout/split-pane"
import { KBContextPanel } from "@/components/kb/kb-context-panel"
import { KnowledgeConsole } from "@/components/kb/knowledge-console"
import { HallucinationPanel } from "@/components/audit/hallucination-panel"
import { VerificationStatusBar } from "@/components/audit/verification-status-bar"
import { ChatDashboard } from "./chat-dashboard"
import { ModelSwitchDialog } from "./model-switch-dialog"
import { useChat, type ModelFallbackEvent } from "@/hooks/use-chat"
import { useChatSend } from "@/hooks/use-chat-send"
import { useConversationsContext } from "@/contexts/conversations-context"
import { useKBContext } from "@/hooks/use-kb-context"
import { useOrchestratedQuery } from "@/hooks/use-orchestrated-query"
import { useContextSources } from "@/hooks/use-context-sources"
import { useSettings } from "@/hooks/use-settings"
import { useModelRouter } from "@/hooks/use-model-router"
import { useModelSwitch } from "@/hooks/use-model-switch"
import { useSmartSuggestions } from "@/hooks/use-smart-suggestions"
import { useVerificationOrchestrator } from "@/hooks/use-verification-orchestrator"
import { useUIMode } from "@/contexts/ui-mode-context"
import { UploadDialog } from "@/components/kb/upload-dialog"
import { useQuery } from "@tanstack/react-query"
import { uploadFile, enableOllama, fetchOllamaStatus, fetchOllamaRecommendations, pullOllamaModel, fetchHealthStatus, retestServices } from "@/lib/api"
import type { ChatMessage } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { uuid } from "@/lib/utils"

const NARROW_MQ = "(max-width: 1024px)"
const narrowSubscribe = (cb: () => void) => {
  const mq = window.matchMedia(NARROW_MQ)
  mq.addEventListener("change", cb)
  return () => mq.removeEventListener("change", cb)
}
const getIsNarrow = () => window.matchMedia(NARROW_MQ).matches

function OllamaCopyRow({ os, cmd, accent }: { os: string; cmd: string; accent: "teal" | "yellow" }) {
  const [copied, setCopied] = useState(false)
  const color = accent === "teal" ? "text-teal-400" : "text-yellow-400"
  const border = accent === "teal" ? "border-teal-500/20" : "border-yellow-500/20"
  return (
    <div className={`flex items-center gap-1.5 rounded border ${border} bg-background/50 px-2 py-1`}>
      <span className={`text-[10px] font-semibold ${color} w-10 shrink-0`}>{os}</span>
      <code className="flex-1 text-[10px] truncate select-all font-mono text-muted-foreground">{cmd}</code>
      <button
        className="shrink-0 rounded p-0.5 hover:bg-muted"
        onClick={() => { navigator.clipboard.writeText(cmd).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) }) }}
        aria-label="Copy"
      >
        {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3 text-muted-foreground" />}
      </button>
    </div>
  )
}

export function ChatPanel() {
  const isNarrow = useSyncExternalStore(narrowSubscribe, getIsNarrow)
  const { isSimple } = useUIMode()
  const {
    active,
    activeId,
    conversations,
    setActiveId,
    create,
    addMessage,
    updateLastMessage,
    updateLastMessageModel,
    updateModel,
    replaceMessages,
    clearMessages,
  } = useConversationsContext()

  const {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    ragMode, setRagMode,
    routingMode, setRoutingMode, cycleRoutingMode,
    autoInject, toggleAutoInject, autoInjectThreshold, setAutoInjectThreshold,
    costSensitivity,
    hallucinationEnabled, toggleHallucinationEnabled,
    memoryExtraction, toggleMemoryExtraction,
    inlineMarkups, toggleInlineMarkups,
    expertVerification, toggleExpertVerification,
    privateModeEnabled, privateModeLevel, togglePrivateMode, changePrivateModeLevel,
  } = useSettings()

  const [fallbackNotice, setFallbackNotice] = useState<string | null>(null)

  const { send, stop, isStreaming } = useChat({
    onMessageStart: (convoId, msg) => addMessage(convoId, msg),
    onMessageUpdate: (convoId, content) => updateLastMessage(convoId, content),
    onModelResolved: (convoId, model) => updateLastMessageModel(convoId, model),
    onModelFallback: (event: ModelFallbackEvent) => {
      const reason = event.originalError === 402
        ? "API credits exhausted"
        : event.originalError
          ? "primary model unavailable"
          : "provider temporarily unreachable"
      const modelName = event.fallbackModel.split("/").pop() ?? event.fallbackModel
      setFallbackNotice(
        `Using fallback model (${modelName}) — ${reason}. Responses may be slower or lower quality.`,
      )
      setTimeout(() => setFallbackNotice(null), 10_000)
    },
    feedbackEnabled: feedbackLoop,
    privateModeLevel,
  })

  const [selectedModel, setSelectedModel] = useState(MODELS[0].id)
  const [showKB, setShowKB] = useState(() => window.innerWidth >= 1024)
  const [pendingChatFiles, setPendingChatFiles] = useState<File[]>([])
  const [focusedClaimIndex, setFocusedClaimIndex] = useState<number | null>(null)

  // Ollama recommendation banner state
  const [ollamaDismissed, setOllamaDismissed] = useState(() => {
    try { return localStorage.getItem("cerid-ollama-dismissed") === "1" } catch { return false }
  })
  const [ollamaShowSetup, setOllamaShowSetup] = useState(false)
  const [ollamaSetupActive, setOllamaSetupActive] = useState(false)
  const [setupSteps, setSetupSteps] = useState<Array<{ label: string; status: "done" | "active" | "pending" }>>([])

  const { data: ollamaHealth } = useQuery({
    queryKey: ["health-ollama-banner"],
    queryFn: fetchHealthStatus,
    refetchInterval: 30_000,
    retry: 1,
    staleTime: 15_000,
  })
  const ollamaLocalCount = ollamaHealth?.pipeline_providers
    ? Object.values(ollamaHealth.pipeline_providers).filter(p => p === "ollama").length
    : 0
  const verificationUnavailable = ollamaHealth?.can_verify === false
  const verificationDegraded = !verificationUnavailable && (ollamaHealth?.degradation_tier === "lite")

  const runOllamaSetup = useCallback(async () => {
    setOllamaSetupActive(true)
    const steps: Array<{ label: string; status: "done" | "active" | "pending" }> = [
      { label: "Checking Ollama...", status: "active" },
      { label: "Detecting hardware...", status: "pending" },
      { label: "Enabling local pipeline", status: "pending" },
      { label: "Setup complete", status: "pending" },
    ]
    setSetupSteps([...steps])

    try {
      // Step 1: Check status
      const status = await fetchOllamaStatus()

      if (!status.reachable) {
        steps[0] = { label: "\u2717 Ollama not running", status: "done" }
        steps[1] = { label: "Install from ollama.com, then run: ollama serve", status: "done" }
        steps[2] = { label: "Click \u201cRetry\u201d below after starting Ollama", status: "done" }
        steps[3] = { label: "Waiting for Ollama...", status: "pending" }
        setSetupSteps([...steps])
        return
      }

      steps[0] = { label: "Ollama detected", status: "done" }

      // Step 2: Get hardware-aware recommendation and pull model
      steps[1] = { ...steps[1], status: "active" }
      setSetupSteps([...steps])

      let modelToPull = status.default_model || "llama3.2:3b"
      try {
        const recs = await fetchOllamaRecommendations()
        modelToPull = recs.recommended
        steps[1] = { label: `${recs.hardware.ram_gb}GB RAM detected \u2014 using ${recs.models.find(m => m.id === recs.recommended)?.name ?? recs.recommended}`, status: "done" }
      } catch {
        steps[1] = { label: `Using ${modelToPull}`, status: "done" }
      }
      setSetupSteps([...steps])

      // Pull if not already installed
      if (!status.models.includes(modelToPull)) {
        steps[2] = { label: `Pulling ${modelToPull}...`, status: "active" }
        setSetupSteps([...steps])
        const pullRes = await pullOllamaModel(modelToPull)
        if (!pullRes.ok) throw new Error("Model pull failed")
        const reader = pullRes.body?.getReader()
        if (reader) {
          while (true) {
            const { done } = await reader.read()
            if (done) break
          }
        }
      }

      // Step 3: Enable with selected model
      steps[2] = { label: "Enabling local pipeline...", status: "active" }
      setSetupSteps([...steps])
      await enableOllama(modelToPull)
      steps[2] = { label: "\u2713 Local pipeline enabled", status: "done" }

      // Step 4: Done
      steps[3] = { label: "\u2713 Setup complete \u2014 pipeline tasks now run locally for $0", status: "done" }
      setSetupSteps([...steps])

      setTimeout(() => {
        setOllamaSetupActive(false)
        setOllamaDismissed(true)
        try { localStorage.setItem("cerid-ollama-dismissed", "1") } catch { /* noop */ }
      }, 3000)
    } catch (err) {
      const activeIdx = steps.findIndex(s => s.status === "active")
      if (activeIdx >= 0) {
        steps[activeIdx] = {
          ...steps[activeIdx],
          status: "done",
          label: "\u2717 Setup failed: " + (err instanceof Error ? err.message : "unknown error"),
        }
      }
      setSetupSteps([...steps])
    }
  }, [])

  const currentModelObj = useMemo(() => MODELS.find((m) => m.id === selectedModel) ?? MODELS[0], [selectedModel])

  // --- Verification orchestrator ---
  const {
    halReport,
    halLoading,
    verification,
    verificationStatusForMsg,
    verificationRecBanner,
    setVerificationRecBanner,
    handleVerifyMessage,
    selectedVerificationMsgId,
    setSelectedVerificationMsgId,
    allVerificationReports,
    claimUpdates,
    expertVerifiedClaims,
    handleClaimUpdate,
  } = useVerificationOrchestrator({
    activeMessages: active?.messages,
    activeId: activeId ?? null,
    isStreaming,
    hallucinationEnabled,
    currentModel: currentModelObj,
    expertVerification,
  })

  // --- KB context ---
  const messages = active?.messages
  const latestUserMessage = useMemo(() => {
    if (!messages) return ""
    const userMsgs = messages.filter((m) => m.role === "user")
    return userMsgs.length > 0 ? userMsgs[userMsgs.length - 1].content : ""
  }, [messages])

  const recentMessages = useMemo(() => {
    if (!messages || messages.length === 0) return undefined
    const userMsgs = messages
      .filter((m) => m.role === "user")
      .slice(-5)
      .map((m) => ({ role: m.role, content: m.content }))
    return userMsgs.length > 0 ? userMsgs : undefined
  }, [messages])

  const kbContext = useKBContext(latestUserMessage, recentMessages)
  const { sources: contextSources, toggleSource } = useContextSources()
  const orchestratedContext = useOrchestratedQuery(latestUserMessage, ragMode, recentMessages, contextSources)
  const { injectedContext, clearInjected } = kbContext

  // Merge orchestrated results into KB results pool for auto-inject.
  // In smart/custom_smart modes, orchestrated results are conversation-aware
  // and higher quality — prefer them over basic KB results.
  // In manual mode, orchestrated results are ignored (user controls injection).
  const effectiveKBResults = useMemo(() => {
    if (ragMode === "manual") return kbContext.results
    return orchestratedContext.results.length > 0
      ? orchestratedContext.results
      : kbContext.results
  }, [ragMode, orchestratedContext.results, kbContext.results])

  // --- Model routing ---
  const { recommendation, dismiss: dismissRec, resetDismiss } = useModelRouter({
    routingMode,
    costSensitivity,
    currentModel: currentModelObj,
    messages: active?.messages ?? [],
    kbInjections: kbContext.injectedContext.length,
  })

  const { pendingSwitch, isSummarizing, initSwitch, executeSwitch, cancelSwitch } =
    useModelSwitch({
      currentModel: currentModelObj,
      messages: active?.messages ?? [],
      onModelChange: (modelId) => {
        setSelectedModel(modelId)
        if (activeId) updateModel(activeId, modelId)
      },
      onReplaceMessages: (msgs) => {
        if (activeId) replaceMessages(activeId, msgs)
      },
      onClearMessages: () => {
        if (activeId) clearMessages(activeId)
      },
    })

  // Sync model selector and reset router when switching conversations
  const activeModel = active?.model
  useEffect(() => {
    if (activeModel) setSelectedModel(activeModel)
    resetDismiss()
    setVerificationRecBanner(null)
  }, [activeId, activeModel, resetDismiss, setVerificationRecBanner])

  // --- Smart suggestions ---
  const injectedArtifactIds = useMemo(
    () => injectedContext.map((r) => r.artifact_id),
    [injectedContext],
  )
  const smartSuggestions = useSmartSuggestions({
    enabled: true,
    injectedArtifactIds,
  })

  // --- Chat send (extracted hook) ---
  const { autoRouteNotice, lastAutoInjectCount, resetAutoInjectCount, handleSend } = useChatSend({
    activeId: activeId ?? null,
    activeMessages: active?.messages,
    create,
    addMessage,
    updateModel,
    replaceMessages,
    send,
    selectedModel,
    setSelectedModel,
    routingMode,
    costSensitivity,
    autoInject,
    autoInjectThreshold,
    injectedContext,
    kbResults: effectiveKBResults,
    clearInjected,
    onBeforeSend: () => setVerificationRecBanner(null),
  })

  // --- Callbacks ---
  const handleModelChange = useCallback(
    (newModel: string) => {
      initSwitch(newModel)
    },
    [initSwitch],
  )

  const handleChatFileDrop = useCallback((files: File[]) => {
    setPendingChatFiles(files)
  }, [])

  const handleChatFileUpload = useCallback(
    async (options: { domain?: string; categorize_mode?: string }) => {
      const files = [...pendingChatFiles]
      setPendingChatFiles([])
      for (const file of files) {
        try {
          await uploadFile(file, { domain: options.domain, categorizeMode: options.categorize_mode })
        } catch { /* upload errors handled by API */ }
      }
    },
    [pendingChatFiles],
  )

  const handleCorrection = useCallback(
    (messageId: string, correction: string) => {
      if (!activeId || !active) return
      const msgs = active.messages
      const idx = msgs.findIndex((m) => m.id === messageId)
      if (idx < 0) return

      // Truncate conversation at the corrected message (remove it and everything after)
      const truncated = msgs.slice(0, idx)
      replaceMessages(activeId, truncated)

      // Create a correction user message and re-send
      const correctionMsg: ChatMessage = {
        id: uuid(),
        role: "user",
        content: `[Correction] ${correction}`,
        timestamp: Date.now(),
      }
      addMessage(activeId, correctionMsg)

      const allMessages: Pick<ChatMessage, "role" | "content">[] = [
        ...truncated,
        correctionMsg,
      ]
      send(activeId, allMessages, selectedModel)
    },
    [activeId, active, replaceMessages, addMessage, send, selectedModel],
  )

  const handleEnrich = useCallback(
    async (messageId: string, content: string) => {
      const MCP_URL = import.meta.env.VITE_MCP_URL || "http://localhost:8888"
      try {
        const res = await fetch(`${MCP_URL}/agent/enrich`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Client-ID": "gui" },
          body: JSON.stringify({ message_id: messageId, content }),
        })
        if (!res.ok) return
        const data = await res.json()
        // If enrichment returned results, inject them as a system note
        if (data.results?.length && activeId) {
          const note: ChatMessage = {
            id: uuid(),
            role: "assistant",
            content: `**Enrichment** (${data.source_count} sources):\n${data.results.map((r: { source: string; snippet: string }) => `- **${r.source}**: ${r.snippet}`).join("\n")}`,
            timestamp: Date.now(),
          }
          addMessage(activeId, note)
        }
      } catch {
        // Enrichment is non-critical — silently fail
      }
    },
    [activeId, addMessage],
  )

  // --- Welcome state (no active conversation) ---
  const recentConversations = conversations.slice(0, 3)

  if (!active) {
    return (
      <div className="flex h-full items-center justify-center bg-background bg-brand-gradient bg-hero-glow">
        <div className="relative flex max-w-md flex-col items-center gap-6 px-6 text-center">
          {/* Subtle pulsing brand glow */}
          <div className="pointer-events-none absolute -top-12 h-32 w-32 animate-pulse rounded-full bg-brand/10 blur-2xl" />
          <div className="relative space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">Cerid <span className="text-brand-gradient">AI</span></h1>
            <p className="text-sm text-muted-foreground">
              Your personal knowledge companion — ask questions, explore your knowledge base, and get verified answers.
            </p>
          </div>

          <Button
            size="lg"
            className="gap-2"
            onClick={() => create(selectedModel)}
          >
            <MessageSquarePlus className="h-4 w-4" />
            New Conversation
          </Button>

          {recentConversations.length > 0 && (
            <div className="w-full space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Recent</p>
              <div className="space-y-1.5">
                {recentConversations.map((c) => {
                  const preview = c.messages[0]?.content?.slice(0, 80) || "Empty conversation"
                  return (
                    <button
                      key={c.id}
                      className="flex w-full items-start gap-2.5 rounded-lg border bg-card px-3 py-2.5 text-left transition-colors hover:bg-accent"
                      onClick={() => setActiveId(c.id)}
                    >
                      <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm">{preview}</p>
                        <p className="text-[11px] text-muted-foreground">
                          {c.messages.length} message{c.messages.length !== 1 ? "s" : ""}
                        </p>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  // --- Render ---
  const chatArea = (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <ChatToolbar
        isNarrow={isNarrow}
        isSimple={isSimple}
        showKB={showKB}
        onToggleKB={() => setShowKB(!showKB)}
        autoInject={autoInject}
        toggleAutoInject={toggleAutoInject}
        autoInjectThreshold={autoInjectThreshold}
        setAutoInjectThreshold={setAutoInjectThreshold}
        hallucinationEnabled={hallucinationEnabled}
        toggleHallucinationEnabled={toggleHallucinationEnabled}
        inlineMarkups={inlineMarkups}
        toggleInlineMarkups={toggleInlineMarkups}
        expertVerification={expertVerification}
        toggleExpertVerification={toggleExpertVerification}
        onVerifyMessage={handleVerifyMessage}
        verificationDegraded={verificationDegraded}
        verificationUnavailable={verificationUnavailable}
        feedbackLoop={feedbackLoop}
        toggleFeedbackLoop={toggleFeedbackLoop}
        memoryExtraction={memoryExtraction}
        toggleMemoryExtraction={toggleMemoryExtraction}
        showDashboard={showDashboard}
        toggleDashboard={toggleDashboard}
        ragMode={ragMode}
        setRagMode={setRagMode}
        routingMode={routingMode}
        setRoutingMode={setRoutingMode}
        cycleRoutingMode={cycleRoutingMode}
        selectedModel={selectedModel}
        onModelChange={handleModelChange}
        privateModeEnabled={privateModeEnabled}
        privateModeLevel={privateModeLevel}
        togglePrivateMode={togglePrivateMode}
        changePrivateModeLevel={changePrivateModeLevel}
        onNewChat={() => create(selectedModel)}
      />

      {/* Credit exhaustion banner */}
      <CreditBanner />

      {/* Service degradation banner */}
      <DegradationBanner />

      {/* Dashboard metrics bar (advanced only) */}
      {!isSimple && showDashboard && (
        <ChatDashboard
          model={selectedModel}
          messages={active?.messages ?? []}
          injectedCount={kbContext.injectedContext.length}
        />
      )}

      {/* Model recommendation banner (advanced only, recommend mode) */}
      {!isSimple && recommendation && routingMode === "recommend" && (
        <div className="flex items-center gap-2 border-b bg-muted/50 px-4 py-1.5 text-xs">
          <Zap className="h-3.5 w-3.5 text-yellow-500" />
          <span className="flex-1">{recommendation.reasoning}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            onClick={() => {
              initSwitch(recommendation.model.id)
              dismissRec()
            }}
          >
            Switch
          </Button>
          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={dismissRec}>
            Dismiss
          </Button>
        </div>
      )}

      {/* V1b: Proactive switch banner (advanced only) */}
      {!isSimple && verificationRecBanner && (
        <div className="flex items-center gap-2 border-b bg-blue-500/10 px-4 py-1.5 text-xs">
          <Zap className="h-3.5 w-3.5 text-blue-500" />
          <span className="flex-1">{verificationRecBanner.reason}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            onClick={() => {
              initSwitch(verificationRecBanner.model.id)
              setVerificationRecBanner(null)
            }}
          >
            Switch
          </Button>
          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => setVerificationRecBanner(null)}>
            Dismiss
          </Button>
        </div>
      )}

      {/* Model switch options dialog */}
      {pendingSwitch && (
        <ModelSwitchDialog
          options={pendingSwitch}
          currentModelId={selectedModel}
          onSelect={executeSwitch}
          onCancel={cancelSwitch}
        />
      )}

      {/* Summarizing indicator */}
      {isSummarizing && (
        <div className="flex items-center gap-2 border-b bg-muted/50 px-4 py-1.5 text-xs">
          <span className="h-2 w-2 animate-pulse rounded-full bg-yellow-500" />
          <span>Summarizing conversation history...</span>
        </div>
      )}

      {/* Expert verification cost indicator */}
      {!isSimple && expertVerification && hallucinationEnabled && verification.phase !== "idle" && verification.phase !== "done" && (
        <div className="flex items-center gap-2 px-3 py-1 text-[11px] text-amber-500 bg-amber-500/5 border-b border-amber-500/10">
          <ShieldCheck className="h-3 w-3" />
          Expert verification active — ~15× standard cost
        </div>
      )}

      {/* Ollama setup progress panel */}
      {ollamaSetupActive && (
        <div className="mx-4 mb-2 rounded-lg border border-teal-500/30 bg-teal-500/10 px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <Cpu className="h-4 w-4 text-teal-400" />
            <span className="text-xs font-medium">
              {setupSteps.some(s => s.label.includes("not running")) ? "Ollama Setup Required" : "Setting up Ollama..."}
            </span>
          </div>
          <div className="space-y-1 font-mono text-[10px] text-muted-foreground">
            {setupSteps.map((step, i) => (
              <div key={i} className="flex items-center gap-1.5">
                {step.status === "done" && step.label.startsWith("\u2717") && <X className="h-3 w-3 text-red-400" />}
                {step.status === "done" && !step.label.startsWith("\u2717") && <Check className="h-3 w-3 text-green-400" />}
                {step.status === "active" && <Loader2Icon className="h-3 w-3 animate-spin" />}
                {step.status === "pending" && <span className="h-3 w-3 text-muted-foreground/30">○</span>}
                <span>{step.label}</span>
              </div>
            ))}
          </div>
          {/* Show install instructions + retry when Ollama not detected */}
          {setupSteps.some(s => s.label.includes("not running")) && (
            <div className="mt-3 space-y-2">
              <p className="text-[11px] text-muted-foreground">Open Terminal (Spotlight → &quot;Terminal&quot;), then copy &amp; paste:</p>
              {[
                { os: "macOS", cmd: "curl -fsSL https://ollama.com/install.sh | sh && open -a Ollama" },
                { os: "Linux", cmd: "curl -fsSL https://ollama.com/install.sh | sh && ollama serve" },
              ].map(({ os, cmd }) => (
                <OllamaCopyRow key={os} os={os} cmd={cmd} accent="teal" />
              ))}
              <div className="flex items-center gap-2">
                <button onClick={() => runOllamaSetup()} className="rounded-md border border-teal-500/40 px-2.5 py-1 text-[11px] font-medium text-teal-400 hover:bg-teal-500/10">
                  Retry
                </button>
                <button onClick={() => { setOllamaSetupActive(false); setOllamaDismissed(true); try { localStorage.setItem("cerid-ollama-dismissed", "1") } catch { /* noop */ } }} className="ml-auto text-[10px] text-muted-foreground hover:text-foreground">
                  Dismiss
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Ollama recommendation — shown when no local stages and user has chatted */}
      {!isSimple && !ollamaSetupActive && ollamaLocalCount === 0 && (active?.messages?.length ?? 0) > 2 && !ollamaDismissed && (
        ollamaShowSetup ? (
          <div className="mx-4 mb-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-4 py-3">
            <p className="text-xs font-medium text-yellow-400">Ollama not detected</p>
            <p className="mt-1 text-[11px] text-muted-foreground">Open Terminal (Spotlight → &quot;Terminal&quot;), then copy &amp; paste:</p>
            <div className="mt-1.5 space-y-1">
              {[
                { os: "macOS", cmd: "curl -fsSL https://ollama.com/install.sh | sh && open -a Ollama" },
                { os: "Linux", cmd: "curl -fsSL https://ollama.com/install.sh | sh && ollama serve" },
              ].map(({ os, cmd }) => (
                <OllamaCopyRow key={os} os={os} cmd={cmd} accent="yellow" />
              ))}
            </div>
            <div className="mt-2 flex items-center gap-2">
              <button onClick={() => runOllamaSetup()} className="rounded-md border border-yellow-500/40 px-2.5 py-1 text-[11px] font-medium text-yellow-400 hover:bg-yellow-500/10">
                I&apos;ve installed it
              </button>
              <button
                onClick={() => { setOllamaShowSetup(false); setOllamaDismissed(true); try { localStorage.setItem("cerid-ollama-dismissed", "1") } catch { /* noop */ } }}
                className="text-[10px] text-muted-foreground hover:text-foreground"
              >
                Dismiss
              </button>
            </div>
          </div>
        ) : (
          <div className="mx-4 mb-2 rounded-lg border border-teal-500/30 bg-teal-500/10 px-4 py-2.5">
            <div className="flex items-center gap-3">
              <Cpu className="h-4 w-4 shrink-0 text-teal-400" />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium">Speed up with local AI</p>
                <p className="text-[11px] text-muted-foreground">Enable Ollama for faster verification — runs locally, no API costs.</p>
              </div>
              <button
                onClick={() => { runOllamaSetup() }}
                className="shrink-0 rounded-md border border-teal-500/40 px-2 py-1 text-[11px] font-medium text-teal-400 hover:bg-teal-500/10"
              >
                Enable
              </button>
              <button
                onClick={() => { setOllamaDismissed(true); try { localStorage.setItem("cerid-ollama-dismissed", "1") } catch { /* noop */ } }}
                className="shrink-0 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )
      )}

      {/* Messages */}
      <ChatMessages
        messages={active?.messages ?? []}
        isStreaming={isStreaming}
        selectedVerificationMsgId={selectedVerificationMsgId}
        verificationStatusForMsg={verificationStatusForMsg}
        halReport={halReport}
        inlineMarkups={inlineMarkups}
        allVerificationReports={allVerificationReports}
        onCorrect={handleCorrection}
        onToggleMarkup={toggleInlineMarkups}
        onClaimFocus={(idx) => {
          setFocusedClaimIndex(idx)
          if (isNarrow) setShowKB(true)
        }}
        onArtifactClick={(artifactId) => {
          kbContext.setSelectedArtifactId(artifactId)
          setShowKB(true)
        }}
        onSelectVerificationMsg={(msgId) => {
          setSelectedVerificationMsgId(msgId ?? null)
          setFocusedClaimIndex(null)
        }}
        onEnrich={handleEnrich}
        onRetry={(userContent) => {
          handleSend(userContent)
          smartSuggestions.clear()
        }}
        onReVerify={handleVerifyMessage}
      />

      {/* Smart KB suggestions (advanced only) */}
      {!isSimple && smartSuggestions.suggestions.length > 0 && !isStreaming && (
        <div className="flex items-center gap-1.5 overflow-x-auto border-t bg-muted/30 px-4 py-1.5">
          <Sparkles className="h-3 w-3 shrink-0 text-muted-foreground" />
          {smartSuggestions.suggestions.map((s) => (
            <Badge
              key={s.artifact_id}
              variant="secondary"
              className="shrink-0 cursor-pointer text-xs hover:bg-accent gap-1.5"
              onClick={() => {
                kbContext.injectResult(s)
                smartSuggestions.dismissSuggestion(s.artifact_id)
              }}
            >
              <span
                className={cn(
                  "inline-block h-1.5 w-1.5 rounded-full",
                  s.relevance >= 0.7 ? "bg-green-500" : s.relevance >= 0.5 ? "bg-yellow-500" : "bg-orange-500",
                )}
              />
              {s.filename}
              <span className="text-[10px] text-muted-foreground tabular-nums">
                {Math.round(s.relevance * 100)}%
              </span>
            </Badge>
          ))}
        </div>
      )}

      {/* Verification status bar (advanced only) */}
      {!isSimple && <VerificationStatusBar
        report={halReport}
        loading={halLoading}
        featureEnabled={hallucinationEnabled}
        streamPhase={verification.phase}
        verifiedCount={verification.verifiedCount}
        totalClaims={verification.totalClaims}
        extractionMethod={verification.extractionMethod}
        streamingClaims={verification.phase !== "idle" && verification.phase !== "done" ? verification.claims : undefined}
        sessionClaimsChecked={verification.sessionClaimsChecked}
        sessionEstCost={verification.sessionEstCost}
        onArtifactClick={(artifactId) => {
          kbContext.setSelectedArtifactId(artifactId)
          setShowKB(true)
        }}
        creditError={verification.creditError}
        onRetry={() => {
          retestServices().catch(() => {})
          handleVerifyMessage()
        }}
      />}

      {/* Auto-route notice (advanced only) */}
      {!isSimple && autoRouteNotice && (
        <div className="flex items-center gap-1.5 border-t bg-yellow-500/10 px-4 py-1">
          <Zap className="h-3 w-3 shrink-0 text-yellow-500" />
          <span className="text-xs text-muted-foreground">{autoRouteNotice}</span>
        </div>
      )}

      {/* Model fallback notice — visible to all users */}
      {fallbackNotice && (
        <div className="flex items-center gap-1.5 border-t bg-amber-500/10 px-4 py-1">
          <AlertTriangle className="h-3 w-3 shrink-0 text-amber-500" />
          <span className="text-xs text-amber-700 dark:text-amber-400">{fallbackNotice}</span>
        </div>
      )}

      {/* Auto-inject indicator (advanced only) */}
      {!isSimple && lastAutoInjectCount > 0 && isStreaming && (
        <div className="flex items-center gap-1.5 border-t bg-primary/5 px-4 py-1">
          <Database className="h-3 w-3 shrink-0 text-primary" />
          <span className="text-xs text-muted-foreground">
            {lastAutoInjectCount} source{lastAutoInjectCount > 1 ? "s" : ""} auto-injected
          </span>
        </div>
      )}

      {/* Chat file drop dialog */}
      <UploadDialog
        files={pendingChatFiles}
        defaultDomain={null}
        onConfirm={handleChatFileUpload}
        onCancel={() => setPendingChatFiles([])}
      />

      {/* Input */}
      <ChatInput
        onSend={(content) => {
          handleSend(content)
          smartSuggestions.clear()
        }}
        onStop={stop}
        isStreaming={isStreaming}
        injectedCount={kbContext.injectedContext.length}
        injectedSources={kbContext.injectedContext.map((r) => ({
          filename: r.filename,
          domain: r.domain,
          content: r.content,
        }))}
        onInputChange={(text) => {
          smartSuggestions.debouncedSearch(text)
          if (lastAutoInjectCount > 0) resetAutoInjectCount()
        }}
        onFileDrop={handleChatFileDrop}
        onArtifactDrop={(artifact) => kbContext.injectResult(artifact)}
      />
    </div>
  )

  // Right column: vertical split — Verification (top) + KB Context (bottom)
  const rightColumn = (
    <Group orientation="vertical" className="h-full" resizeTargetMinimumSize={{ coarse: 22, fine: 5 }}>
      <Panel defaultSize={33} minSize={15}>
        <HallucinationPanel
          report={halReport}
          loading={halLoading}
          featureEnabled={hallucinationEnabled}
          conversationId={activeId ?? undefined}
          streamingClaims={verification.phase !== "idle" && verification.phase !== "done" ? verification.claims : undefined}
          verificationPhase={verification.phase}
          activityLog={verification.activityLog}
          focusedClaimIndex={focusedClaimIndex}
          onClaimFocus={setFocusedClaimIndex}
          onClose={() => setShowKB(false)}
          expertVerification={expertVerification}
          toggleExpertVerification={toggleExpertVerification}
          inlineMarkups={inlineMarkups}
          toggleInlineMarkups={toggleInlineMarkups}
          claimUpdates={claimUpdates}
          expertVerifiedClaims={expertVerifiedClaims}
          onClaimUpdate={handleClaimUpdate}
        />
      </Panel>
      <PanelSeparator className="h-1 bg-border transition-colors hover:bg-primary/20 active:bg-primary/30" />
      <Panel defaultSize={67} minSize={20}>
        {ragMode === "manual" ? (
          <KBContextPanel
            {...kbContext}
            onClose={() => setShowKB(false)}
            contextSources={contextSources}
            toggleKB={() => toggleSource("kb")}
            toggleMemory={() => toggleSource("memory")}
            toggleExternal={() => toggleSource("external")}
          />
        ) : (
          <KnowledgeConsole
            {...orchestratedContext}
            toggleKB={() => toggleSource("kb")}
            toggleMemory={() => toggleSource("memory")}
            toggleExternal={() => toggleSource("external")}
            ragMode={ragMode}
            onRagModeChange={setRagMode}
            onClose={() => setShowKB(false)}
          />
        )}
      </Panel>
    </Group>
  )

  // Simple mode: no right column at all
  if (isSimple) return chatArea

  // On narrow viewports, show KB as a bottom drawer instead of split pane
  if (isNarrow) {
    return (
      <>
        {chatArea}
        <Sheet open={showKB} onOpenChange={setShowKB}>
          <SheetContent side="bottom" className="h-[70vh] flex flex-col">
            <SheetTitle className="sr-only">Knowledge Context</SheetTitle>
            <div className="min-h-0 flex-1 overflow-auto">{rightColumn}</div>
          </SheetContent>
        </Sheet>
      </>
    )
  }

  return (
    <SplitPane
      left={chatArea}
      right={rightColumn}
      showRight={showKB}
    />
  )
}
