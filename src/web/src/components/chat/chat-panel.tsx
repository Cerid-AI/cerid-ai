import { useRef, useEffect, useCallback, useState } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Plus, PanelLeftClose, PanelLeft } from "lucide-react"
import { MessageBubble } from "./message-bubble"
import { ChatInput } from "./chat-input"
import { ModelSelect } from "./model-select"
import { ConversationList } from "./conversation-list"
import { useChat } from "@/hooks/use-chat"
import { useConversations } from "@/hooks/use-conversations"
import type { ChatMessage } from "@/lib/types"
import { MODELS } from "@/lib/types"

export function ChatPanel() {
  const {
    conversations,
    active,
    activeId,
    setActiveId,
    create,
    addMessage,
    updateLastMessage,
    remove,
  } = useConversations()

  const { send, stop, isStreaming } = useChat({
    onMessageStart: (convoId, msg) => addMessage(convoId, msg),
    onMessageUpdate: (convoId, content) => updateLastMessage(convoId, content),
  })

  const scrollRef = useRef<HTMLDivElement>(null)
  const [selectedModel, setSelectedModel] = useState(MODELS[0].id)
  const [showHistory, setShowHistory] = useState(true)
  const model = active?.model ?? selectedModel

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [active?.messages])

  const handleSend = useCallback(
    (content: string) => {
      let convoId = activeId
      if (!convoId) {
        convoId = create(model)
      }

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content,
        timestamp: Date.now(),
      }
      addMessage(convoId, userMsg)

      const allMessages = [...(active?.messages ?? []), userMsg]
      send(convoId, allMessages, model)
    },
    [activeId, active, model, addMessage, create, send]
  )

  const handleModelChange = useCallback(
    (newModel: string) => {
      setSelectedModel(newModel)
      if (!activeId) {
        create(newModel)
      }
    },
    [activeId, create]
  )

  return (
    <div className="flex h-full">
      {/* Conversation history */}
      {showHistory && (
        <div className="flex w-64 flex-col border-r">
          <div className="flex items-center gap-2 border-b px-3 py-2">
            <span className="flex-1 text-sm font-medium">History</span>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setShowHistory(false)}>
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

      {/* Chat area */}
      <div className="flex flex-1 flex-col">
        {/* Toolbar */}
        <div className="flex items-center gap-2 border-b px-4 py-2">
          {!showHistory && (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setShowHistory(true)}>
              <PanelLeft className="h-4 w-4" />
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => create(model)}>
            <Plus className="mr-1 h-4 w-4" />
            New chat
          </Button>
          <div className="flex-1" />
          <ModelSelect value={model} onChange={handleModelChange} />
        </div>

        {/* Messages */}
        <ScrollArea className="flex-1 px-4" ref={scrollRef}>
          <div className="mx-auto max-w-3xl py-4">
            {(!active || active.messages.length === 0) && (
              <div className="flex items-center justify-center py-20 text-muted-foreground">
                <p>Start a conversation...</p>
              </div>
            )}
            {active?.messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))}
          </div>
        </ScrollArea>

        {/* Input */}
        <ChatInput onSend={handleSend} onStop={stop} isStreaming={isStreaming} />
      </div>
    </div>
  )
}
