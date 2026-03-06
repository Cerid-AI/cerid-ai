// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useRef, useEffect, useCallback, useState, useMemo, useSyncExternalStore } from "react"
import { Group, Panel, Separator as PanelSeparator } from "react-resizable-panels"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Plus, Database, Rss, LayoutDashboard, Zap, Sparkles, Shield, MoreVertical } from "lucide-react"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { Badge } from "@/components/ui/badge"
import { MessageBubble, type MessageVerificationStatus } from "./message-bubble"
import { ModelSwitchDivider } from "./model-switch-divider"
import { ChatInput } from "./chat-input"
import { ModelSelect } from "./model-select"
import { SplitPane } from "@/components/layout/split-pane"
import { KBContextPanel } from "@/components/kb/kb-context-panel"
import { HallucinationPanel } from "@/components/audit/hallucination-panel"
import { VerificationStatusBar } from "@/components/audit/verification-status-bar"
import { ChatDashboard } from "./chat-dashboard"
import { ModelSwitchDialog } from "./model-switch-dialog"
import { useChat } from "@/hooks/use-chat"
import { useConversationsContext } from "@/contexts/conversations-context"
import { useKBContext } from "@/hooks/use-kb-context"
import { useSettings } from "@/hooks/use-settings"
import { useModelRouter } from "@/hooks/use-model-router"
import { useModelSwitch } from "@/hooks/use-model-switch"
import { useSmartSuggestions } from "@/hooks/use-smart-suggestions"
import { useVerificationStream } from "@/hooks/use-verification-stream"
import { fetchHallucinationReport } from "@/lib/api"
import type { ChatMessage, SourceRef, HallucinationReport } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { cn, uuid } from "@/lib/utils"

const NARROW_MQ = "(max-width: 1024px)"
const narrowSubscribe = (cb: () => void) => {
  const mq = window.matchMedia(NARROW_MQ)
  mq.addEventListener("change", cb)
  return () => mq.removeEventListener("change", cb)
}
const getIsNarrow = () => window.matchMedia(NARROW_MQ).matches

export function ChatPanel() {
  const isNarrow = useSyncExternalStore(narrowSubscribe, getIsNarrow)
  const {
    active,
    activeId,
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
    autoModelSwitch, toggleAutoModelSwitch,
    autoInject, autoInjectThreshold,
    costSensitivity,
    hallucinationEnabled, toggleHallucinationEnabled,
  } = useSettings()

  const { send, stop, isStreaming } = useChat({
    onMessageStart: (convoId, msg) => addMessage(convoId, msg),
    onMessageUpdate: (convoId, content) => updateLastMessage(convoId, content),
    onModelResolved: (convoId, model) => updateLastMessageModel(convoId, model),
    feedbackEnabled: feedbackLoop,
  })

  const scrollRef = useRef<HTMLDivElement>(null)
  const [selectedModel, setSelectedModel] = useState(MODELS[0].id)
  const [showKB, setShowKB] = useState(() => window.innerWidth >= 1024)
  const [lastAutoInjectCount, setLastAutoInjectCount] = useState(0)

  // --- Verification (streaming + fallback) ---
  const [savedReport, setSavedReport] = useState<HallucinationReport | null>(null)
  const [savedReportLoading, setSavedReportLoading] = useState(false)

  // Get the latest assistant response text for streaming verification (uses `messages` defined below)
  const activeMessages = active?.messages
  const latestAssistantText = useMemo(() => {
    if (!activeMessages) return null
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant")
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].content : null
  }, [activeMessages])

  // Trigger key: increments each time a new assistant message finishes streaming
  const streamTriggerKey = useMemo(() => {
    if (!activeMessages || isStreaming) return 0
    return activeMessages.filter((m) => m.role === "assistant").length
  }, [activeMessages, isStreaming])

  // Last user message for evasion detection
  const latestUserQuery = useMemo(() => {
    if (!activeMessages) return undefined
    const userMsgs = activeMessages.filter((m) => m.role === "user")
    return userMsgs.length > 0 ? userMsgs[userMsgs.length - 1].content : undefined
  }, [activeMessages])

  // Actual model that generated the latest assistant response (not the dropdown selection)
  const latestAssistantModel = useMemo(() => {
    if (!activeMessages) return undefined
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant")
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].model : undefined
  }, [activeMessages])

  // Prior assistant context for history consistency checking (last 3 prior responses)
  const priorAssistantContext = useMemo(() => {
    if (!activeMessages) return undefined
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant")
    if (assistantMsgs.length < 2) return undefined
    // Exclude the latest (being verified), take up to 3 prior, truncate each
    return assistantMsgs.slice(-4, -1).map((m) => ({
      role: "assistant" as const,
      content: m.content.slice(0, 2000),
    }))
  }, [activeMessages])

  // Streaming verification hook
  const verification = useVerificationStream(
    latestAssistantText,
    activeId ?? null,
    hallucinationEnabled,
    streamTriggerKey,
    latestAssistantModel,
    latestUserQuery,
    priorAssistantContext,
  )

  // Fetch saved report when switching to a conversation (fallback)
  useEffect(() => {
    if (!activeId || !hallucinationEnabled) {
      setSavedReport(null)
      return
    }
    // Only fetch saved report if streaming hasn't started
    if (verification.phase !== "idle") return

    let cancelled = false
    setSavedReportLoading(true)
    fetchHallucinationReport(activeId)
      .then((r) => { if (!cancelled) { setSavedReport(r); setSavedReportLoading(false) } })
      .catch(() => { if (!cancelled) setSavedReportLoading(false) })
    return () => { cancelled = true }
  }, [activeId, hallucinationEnabled]) // eslint-disable-line react-hooks/exhaustive-deps

  // Unified report: prefer streaming report when available, fallback to saved
  const halReport = verification.report ?? savedReport
  const halLoading = verification.loading || savedReportLoading

  const messages = active?.messages
  const latestUserMessage = useMemo(() => {
    if (!messages) return ""
    const userMsgs = messages.filter((m) => m.role === "user")
    return userMsgs.length > 0 ? userMsgs[userMsgs.length - 1].content : ""
  }, [messages])

  // Recent conversation messages for KB query enrichment (last 5 user messages)
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

  // Compute per-message verification status for the latest assistant message
  const lastAssistantMsgId = useMemo(() => {
    if (!activeMessages) return null
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant" && m.content)
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].id : null
  }, [activeMessages])

  const verificationStatusForMsg = useMemo((): MessageVerificationStatus => {
    if (!hallucinationEnabled || !lastAssistantMsgId) return null
    if (verification.loading) return { state: "loading" }
    if (halReport && !halReport.skipped && halReport.summary.total > 0) {
      return {
        state: "done",
        verified: halReport.summary.verified,
        unverified: halReport.summary.unverified,
        uncertain: halReport.summary.uncertain,
        total: halReport.summary.total,
      }
    }
    return null
  }, [hallucinationEnabled, lastAssistantMsgId, verification.loading, halReport])

  const currentModelObj = useMemo(() => MODELS.find((m) => m.id === selectedModel) ?? MODELS[0], [selectedModel])
  const { recommendation, dismiss: dismissRec, resetDismiss } = useModelRouter({
    enabled: autoModelSwitch,
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
  }, [activeId, activeModel, resetDismiss])

  const injectedArtifactIds = useMemo(
    () => injectedContext.map((r) => r.artifact_id),
    [injectedContext],
  )
  const smartSuggestions = useSmartSuggestions({
    enabled: true,
    injectedArtifactIds,
  })

  useEffect(() => {
    const viewport = scrollRef.current?.querySelector<HTMLDivElement>(
      "[data-radix-scroll-area-viewport]",
    )
    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight
    }
  }, [active?.messages])

  const handleSend = useCallback(
    (content: string) => {
      let convoId = activeId
      if (!convoId) {
        convoId = create(selectedModel)
      }

      const userMsg: ChatMessage = {
        id: uuid(),
        role: "user",
        content,
        timestamp: Date.now(),
      }
      addMessage(convoId, userMsg)

      // Combine manually injected + auto-injected context
      const manuallyInjected = [...injectedContext]
      const injectedIds = new Set(manuallyInjected.map((r) => r.artifact_id))

      // Auto-inject high-confidence KB results at send time
      let autoInjectedCount = 0
      if (autoInject && kbContext.results.length > 0) {
        const candidates = kbContext.results
          .filter((r) => r.relevance >= autoInjectThreshold && !injectedIds.has(r.artifact_id))
          .slice(0, 3)
        for (const c of candidates) {
          manuallyInjected.push(c)
          autoInjectedCount++
        }
      }

      let sourcesForAssistant: SourceRef[] | undefined
      const allMessages: Pick<ChatMessage, "role" | "content">[] = []
      if (manuallyInjected.length > 0) {
        sourcesForAssistant = manuallyInjected.map((r) => ({
          artifact_id: r.artifact_id,
          filename: r.filename,
          domain: r.domain,
          sub_category: r.sub_category,
          relevance: r.relevance,
          chunk_index: r.chunk_index,
          tags: r.tags,
          quality_score: r.quality_score,
        }))

        const contextParts = manuallyInjected.map(
          (r) => `--- Source: ${r.filename} (${r.domain}) ---\n${r.content}`,
        )
        allMessages.push({
          role: "system",
          content: `The user's knowledge base contains the following relevant context. Use it to inform your response:\n\n${contextParts.join("\n\n")}`,
        })
        clearInjected()
      }

      if (autoInjectedCount > 0) {
        setLastAutoInjectCount(autoInjectedCount)
      }

      allMessages.push(...(active?.messages ?? []), userMsg)
      send(convoId, allMessages, selectedModel, sourcesForAssistant)
    },
    [activeId, active, selectedModel, addMessage, create, send, injectedContext, clearInjected, autoInject, autoInjectThreshold, kbContext.results],
  )

  const handleModelChange = useCallback(
    (newModel: string) => {
      initSwitch(newModel)
    },
    [initSwitch],
  )

  const chatArea = (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b px-4 py-2">
        <Button variant="ghost" size="sm" onClick={() => create(selectedModel)}>
          <Plus className="mr-1 h-4 w-4" />
          {!isNarrow && "New chat"}
        </Button>
        <div className="flex-1" />
        <TooltipProvider delayDuration={0}>
          {/* Always visible: KB toggle */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn("h-8 w-8", showKB && "text-green-500")}
                onClick={() => setShowKB(!showKB)}
                aria-label={showKB ? "Hide knowledge context" : "Show knowledge context"}
              >
                <Database className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {showKB ? "Hide knowledge context" : "Show knowledge context"}
            </TooltipContent>
          </Tooltip>
          {/* Always visible: Verification */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn("h-8 w-8", hallucinationEnabled && "text-green-500")}
                onClick={toggleHallucinationEnabled}
                aria-label={hallucinationEnabled ? "Disable response verification" : "Enable response verification"}
              >
                <Shield className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {hallucinationEnabled ? "Response verification: ON" : "Response verification: OFF"}
            </TooltipContent>
          </Tooltip>
          {/* Wide viewport: show all buttons inline */}
          {!isNarrow && (
            <>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={cn("h-8 w-8", feedbackLoop && "text-green-500")}
                    onClick={toggleFeedbackLoop}
                    aria-label={feedbackLoop ? "Disable feedback loop" : "Enable feedback loop"}
                  >
                    <Rss className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {feedbackLoop ? "Feedback loop: ON (responses saved to KB)" : "Feedback loop: OFF"}
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={cn("h-8 w-8", showDashboard && "text-green-500")}
                    onClick={toggleDashboard}
                    aria-label={showDashboard ? "Hide metrics dashboard" : "Show metrics dashboard"}
                  >
                    <LayoutDashboard className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {showDashboard ? "Hide metrics dashboard" : "Show metrics dashboard"}
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={cn("h-8 w-8", autoModelSwitch && "text-green-500")}
                    onClick={toggleAutoModelSwitch}
                    aria-label={autoModelSwitch ? "Disable smart model routing" : "Enable smart model routing"}
                  >
                    <Zap className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {autoModelSwitch ? "Smart model routing: ON" : "Smart model routing: OFF"}
                </TooltipContent>
              </Tooltip>
            </>
          )}
          {/* Narrow viewport: overflow menu */}
          {isNarrow && (
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="More options">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-48">
                <button
                  className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", feedbackLoop && "text-green-500")}
                  onClick={toggleFeedbackLoop}
                >
                  <Rss className="h-4 w-4" />
                  {feedbackLoop ? "Feedback: ON" : "Feedback: OFF"}
                </button>
                <button
                  className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", showDashboard && "text-green-500")}
                  onClick={toggleDashboard}
                >
                  <LayoutDashboard className="h-4 w-4" />
                  {showDashboard ? "Dashboard: ON" : "Dashboard: OFF"}
                </button>
                <button
                  className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", autoModelSwitch && "text-green-500")}
                  onClick={toggleAutoModelSwitch}
                >
                  <Zap className="h-4 w-4" />
                  {autoModelSwitch ? "Smart route: ON" : "Smart route: OFF"}
                </button>
              </PopoverContent>
            </Popover>
          )}
        </TooltipProvider>
        <ModelSelect value={selectedModel} onChange={handleModelChange} />
      </div>

      {/* Dashboard metrics bar */}
      {showDashboard && (
        <ChatDashboard
          model={selectedModel}
          messages={active?.messages ?? []}
          injectedCount={kbContext.injectedContext.length}
        />
      )}

      {/* Model recommendation banner */}
      {recommendation && (
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

      {/* Messages */}
      <ScrollArea className="min-h-0 flex-1 px-4" ref={scrollRef}>
        <div className="mx-auto max-w-4xl py-4">
          {(!active || active.messages.length === 0) && (
            <div className="flex items-center justify-center py-20 text-muted-foreground">
              <p>Start a conversation...</p>
            </div>
          )}
          {active?.messages.map((msg, i) => {
            let divider: React.ReactNode = null
            if (msg.role === "assistant" && msg.model) {
              const prevAssistant = active.messages
                .slice(0, i)
                .findLast((m) => m.role === "assistant" && m.model)
              if (prevAssistant?.model && prevAssistant.model !== msg.model) {
                divider = (
                  <ModelSwitchDivider
                    key={`switch-${msg.id}`}
                    fromModelId={prevAssistant.model}
                    toModelId={msg.model}
                  />
                )
              }
            }
            return (
              <div key={msg.id}>
                {divider}
                <MessageBubble
                  message={msg}
                  verificationStatus={msg.id === lastAssistantMsgId ? verificationStatusForMsg : undefined}
                />
              </div>
            )
          })}
        </div>
      </ScrollArea>

      {/* Smart KB suggestions */}
      {smartSuggestions.suggestions.length > 0 && !isStreaming && (
        <div className="flex items-center gap-1.5 overflow-x-auto border-t bg-muted/30 px-4 py-1.5">
          <Sparkles className="h-3 w-3 shrink-0 text-muted-foreground" />
          {smartSuggestions.suggestions.map((s) => (
            <Badge
              key={s.artifact_id}
              variant="secondary"
              className="shrink-0 cursor-pointer text-xs hover:bg-accent"
              onClick={() => {
                kbContext.injectResult(s)
                smartSuggestions.dismissSuggestion(s.artifact_id)
              }}
            >
              {s.filename}
            </Badge>
          ))}
        </div>
      )}

      {/* Verification status bar */}
      <VerificationStatusBar
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
      />

      {/* Auto-inject indicator */}
      {lastAutoInjectCount > 0 && isStreaming && (
        <div className="flex items-center gap-1.5 border-t bg-primary/5 px-4 py-1">
          <Database className="h-3 w-3 shrink-0 text-primary" />
          <span className="text-xs text-muted-foreground">
            {lastAutoInjectCount} source{lastAutoInjectCount > 1 ? "s" : ""} auto-injected
          </span>
        </div>
      )}

      {/* Input */}
      <ChatInput
        onSend={(content) => {
          handleSend(content)
          smartSuggestions.clear()
        }}
        onStop={stop}
        isStreaming={isStreaming}
        injectedCount={kbContext.injectedContext.length}
        onInputChange={(text) => {
          smartSuggestions.debouncedSearch(text)
          if (lastAutoInjectCount > 0) setLastAutoInjectCount(0)
        }}
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
        />
      </Panel>
      <PanelSeparator className="h-1 bg-border transition-colors hover:bg-primary/20 active:bg-primary/30" />
      <Panel defaultSize={67} minSize={20}>
        <KBContextPanel {...kbContext} onClose={() => setShowKB(false)} />
      </Panel>
    </Group>
  )

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
