// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useCallback, useState, useMemo, useSyncExternalStore } from "react"
import { Group, Panel, Separator as PanelSeparator } from "react-resizable-panels"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Database, Zap, Sparkles, MessageSquarePlus, Clock, ShieldCheck } from "lucide-react"
import { ChatToolbar } from "./chat-toolbar"
import { ChatMessages } from "./chat-messages"
import { ChatInput } from "./chat-input"
import { SplitPane } from "@/components/layout/split-pane"
import { KBContextPanel } from "@/components/kb/kb-context-panel"
import { HallucinationPanel } from "@/components/audit/hallucination-panel"
import { VerificationStatusBar } from "@/components/audit/verification-status-bar"
import { ChatDashboard } from "./chat-dashboard"
import { ModelSwitchDialog } from "./model-switch-dialog"
import { useChat } from "@/hooks/use-chat"
import { useChatSend } from "@/hooks/use-chat-send"
import { useConversationsContext } from "@/contexts/conversations-context"
import { useKBContext } from "@/hooks/use-kb-context"
import { useSettings } from "@/hooks/use-settings"
import { useModelRouter } from "@/hooks/use-model-router"
import { useModelSwitch } from "@/hooks/use-model-switch"
import { useSmartSuggestions } from "@/hooks/use-smart-suggestions"
import { useVerificationOrchestrator } from "@/hooks/use-verification-orchestrator"
import { useUIMode } from "@/contexts/ui-mode-context"
import { UploadDialog } from "@/components/kb/upload-dialog"
import { uploadFile } from "@/lib/api"
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
    routingMode, setRoutingMode, cycleRoutingMode,
    autoInject, toggleAutoInject, autoInjectThreshold, setAutoInjectThreshold,
    costSensitivity,
    hallucinationEnabled, toggleHallucinationEnabled,
    memoryExtraction, toggleMemoryExtraction,
    inlineMarkups, toggleInlineMarkups,
    expertVerification, toggleExpertVerification,
  } = useSettings()

  const { send, stop, isStreaming } = useChat({
    onMessageStart: (convoId, msg) => addMessage(convoId, msg),
    onMessageUpdate: (convoId, content) => updateLastMessage(convoId, content),
    onModelResolved: (convoId, model) => updateLastMessageModel(convoId, model),
    feedbackEnabled: feedbackLoop,
  })

  const [selectedModel, setSelectedModel] = useState(MODELS[0].id)
  const [showKB, setShowKB] = useState(() => window.innerWidth >= 1024)
  const [pendingChatFiles, setPendingChatFiles] = useState<File[]>([])
  const [focusedClaimIndex, setFocusedClaimIndex] = useState<number | null>(null)

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
  const { injectedContext, clearInjected } = kbContext

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
    send,
    selectedModel,
    setSelectedModel,
    routingMode,
    costSensitivity,
    autoInject,
    autoInjectThreshold,
    injectedContext,
    kbResults: kbContext.results,
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

  // --- Welcome state (no active conversation) ---
  const recentConversations = conversations.slice(0, 3)

  if (!active) {
    return (
      <div className="flex h-full items-center justify-center bg-background bg-brand-gradient">
        <div className="relative flex max-w-md flex-col items-center gap-6 px-6 text-center">
          {/* Subtle pulsing brand glow */}
          <div className="pointer-events-none absolute -top-12 h-32 w-32 animate-pulse rounded-full bg-brand/10 blur-2xl" />
          <div className="relative space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">Cerid <span className="text-brand">AI</span></h1>
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
        feedbackLoop={feedbackLoop}
        toggleFeedbackLoop={toggleFeedbackLoop}
        memoryExtraction={memoryExtraction}
        toggleMemoryExtraction={toggleMemoryExtraction}
        showDashboard={showDashboard}
        toggleDashboard={toggleDashboard}
        routingMode={routingMode}
        setRoutingMode={setRoutingMode}
        cycleRoutingMode={cycleRoutingMode}
        selectedModel={selectedModel}
        onModelChange={handleModelChange}
        onNewChat={() => create(selectedModel)}
      />

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
          setSelectedVerificationMsgId(msgId)
          setFocusedClaimIndex(null)
        }}
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
      />}

      {/* Auto-route notice (advanced only) */}
      {!isSimple && autoRouteNotice && (
        <div className="flex items-center gap-1.5 border-t bg-yellow-500/10 px-4 py-1">
          <Zap className="h-3 w-3 shrink-0 text-yellow-500" />
          <span className="text-xs text-muted-foreground">{autoRouteNotice}</span>
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
          focusedClaimIndex={focusedClaimIndex}
          onClaimFocus={setFocusedClaimIndex}
          onClose={() => setShowKB(false)}
          expertVerification={expertVerification}
          toggleExpertVerification={toggleExpertVerification}
          inlineMarkups={inlineMarkups}
          toggleInlineMarkups={toggleInlineMarkups}
        />
      </Panel>
      <PanelSeparator className="h-1 bg-border transition-colors hover:bg-primary/20 active:bg-primary/30" />
      <Panel defaultSize={67} minSize={20}>
        <KBContextPanel {...kbContext} onClose={() => setShowKB(false)} />
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
