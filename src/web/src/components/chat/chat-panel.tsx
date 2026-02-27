// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useRef, useEffect, useCallback, useState, useMemo } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Plus, PanelLeftClose, PanelLeft, Database, Rss, LayoutDashboard, Zap, Sparkles } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { MessageBubble } from "./message-bubble"
import { ModelSwitchDivider } from "./model-switch-divider"
import { ChatInput } from "./chat-input"
import { ModelSelect } from "./model-select"
import { ConversationList } from "./conversation-list"
import { SplitPane } from "@/components/layout/split-pane"
import { KBContextPanel } from "@/components/kb/kb-context-panel"
import { HallucinationPanel } from "@/components/audit/hallucination-panel"
import { ChatDashboard } from "./chat-dashboard"
import { useChat } from "@/hooks/use-chat"
import { useConversations } from "@/hooks/use-conversations"
import { useKBContext } from "@/hooks/use-kb-context"
import { useSettings } from "@/hooks/use-settings"
import { useModelRouter } from "@/hooks/use-model-router"
import { useSmartSuggestions } from "@/hooks/use-smart-suggestions"
import type { ChatMessage, SourceRef } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { cn } from "@/lib/utils"

export function ChatPanel() {
  const {
    conversations,
    active,
    activeId,
    setActiveId,
    create,
    addMessage,
    updateLastMessage,
    updateModel,
    remove,
  } = useConversations()

  const {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    autoModelSwitch, toggleAutoModelSwitch,
    costSensitivity,
  } = useSettings()

  const { send, stop, isStreaming } = useChat({
    onMessageStart: (convoId, msg) => addMessage(convoId, msg),
    onMessageUpdate: (convoId, content) => updateLastMessage(convoId, content),
    feedbackEnabled: feedbackLoop,
  })

  const scrollRef = useRef<HTMLDivElement>(null)
  const [selectedModel, setSelectedModel] = useState(MODELS[0].id)
  const [showHistory, setShowHistory] = useState(() => window.innerWidth >= 1024)
  const [showKB, setShowKB] = useState(() => window.innerWidth >= 1024)

  const messages = active?.messages
  const latestUserMessage = useMemo(() => {
    if (!messages) return ""
    const userMsgs = messages.filter((m) => m.role === "user")
    return userMsgs.length > 0 ? userMsgs[userMsgs.length - 1].content : ""
  }, [messages])

  const kbContext = useKBContext(latestUserMessage)
  const { injectedContext, clearInjected } = kbContext

  const currentModelObj = useMemo(() => MODELS.find((m) => m.id === selectedModel) ?? MODELS[0], [selectedModel])
  const { recommendation, dismiss: dismissRec, resetDismiss } = useModelRouter({
    enabled: autoModelSwitch,
    costSensitivity,
    currentModel: currentModelObj,
    messages: active?.messages ?? [],
    kbInjections: kbContext.injectedContext.length,
  })

  // Sync model selector and reset router when switching conversations
  useEffect(() => {
    if (active?.model) setSelectedModel(active.model)
    resetDismiss()
  }, [activeId]) // eslint-disable-line react-hooks/exhaustive-deps

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
        id: crypto.randomUUID(),
        role: "user",
        content,
        timestamp: Date.now(),
      }
      addMessage(convoId, userMsg)

      let sourcesForAssistant: SourceRef[] | undefined
      const allMessages: Pick<ChatMessage, "role" | "content">[] = []
      if (injectedContext.length > 0) {
        // Capture lightweight source refs before clearing
        sourcesForAssistant = injectedContext.map((r) => ({
          artifact_id: r.artifact_id,
          filename: r.filename,
          domain: r.domain,
          sub_category: r.sub_category,
          relevance: r.relevance,
          chunk_index: r.chunk_index,
          tags: r.tags,
        }))

        const contextParts = injectedContext.map(
          (r) => `--- Source: ${r.filename} (${r.domain}) ---\n${r.content}`,
        )
        allMessages.push({
          role: "system",
          content: `The user's knowledge base contains the following relevant context. Use it to inform your response:\n\n${contextParts.join("\n\n")}`,
        })
        clearInjected()
      }

      allMessages.push(...(active?.messages ?? []), userMsg)
      send(convoId, allMessages, selectedModel, sourcesForAssistant)
    },
    [activeId, active, selectedModel, addMessage, create, send, injectedContext, clearInjected],
  )

  const handleModelChange = useCallback(
    (newModel: string) => {
      setSelectedModel(newModel)
      if (activeId) {
        updateModel(activeId, newModel)
      }
    },
    [activeId, updateModel],
  )

  const chatArea = (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b px-4 py-2">
        {!showHistory && (
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setShowHistory(true)} aria-label="Show conversation history">
            <PanelLeft className="h-4 w-4" />
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => create(selectedModel)}>
          <Plus className="mr-1 h-4 w-4" />
          New chat
        </Button>
        <div className="flex-1" />
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn("h-8 w-8", feedbackLoop && "text-primary")}
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
                className={cn("h-8 w-8", showDashboard && "text-primary")}
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
                className={cn("h-8 w-8", autoModelSwitch && "text-primary")}
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
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn("h-8 w-8", showKB && "text-primary")}
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
              handleModelChange(recommendation.model.id)
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

      {/* Messages */}
      <ScrollArea className="min-h-0 flex-1 px-4" ref={scrollRef}>
        <div className="mx-auto max-w-3xl py-4">
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
                <MessageBubble message={msg} />
              </div>
            )
          })}
          {/* Hallucination panel after messages — refreshKey triggers re-fetch after new messages */}
          {activeId && !isStreaming && (
            <HallucinationPanel conversationId={activeId} refreshKey={active?.messages.length ?? 0} />
          )}
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

      {/* Input */}
      <ChatInput
        onSend={(content) => {
          handleSend(content)
          smartSuggestions.clear()
        }}
        onStop={stop}
        isStreaming={isStreaming}
        injectedCount={kbContext.injectedContext.length}
        onInputChange={smartSuggestions.debouncedSearch}
      />
    </div>
  )

  return (
    <div className="flex h-full">
      {/* Conversation history */}
      {showHistory && (
        <div className="flex w-64 flex-col border-r">
          <div className="flex items-center gap-2 border-b px-3 py-2">
            <span className="flex-1 text-sm font-medium">History</span>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setShowHistory(false)} aria-label="Hide conversation history">
              <PanelLeftClose className="h-4 w-4" />
            </Button>
          </div>
          <ConversationList
            conversations={conversations}
            activeId={activeId}
            onSelect={setActiveId}
            onDelete={remove}
          />
        </div>
      )}

      {/* Chat + KB split pane */}
      <div className="flex-1">
        <SplitPane
          left={chatArea}
          right={<KBContextPanel {...kbContext} onClose={() => setShowKB(false)} />}
          showRight={showKB}
        />
      </div>
    </div>
  )
}