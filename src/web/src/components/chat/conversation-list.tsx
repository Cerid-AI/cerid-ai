// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Conversation } from "@/lib/types"

interface ConversationListProps {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

export function ConversationList({ conversations, activeId, onSelect, onDelete }: ConversationListProps) {
  if (conversations.length === 0) {
    return (
      <div className="p-4 text-center text-sm text-muted-foreground">
        No conversations yet
      </div>
    )
  }

  return (
    <ScrollArea className="flex-1">
      <div className="space-y-1 p-2">
        {conversations.map((convo) => (
          <div
            key={convo.id}
            role="button"
            tabIndex={0}
            className={cn(
              "group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-muted",
              activeId === convo.id && "bg-muted"
            )}
            onClick={() => onSelect(convo.id)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(convo.id) } }}
          >
            <span className="flex-1 truncate">{convo.title}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Delete conversation"
              className="h-6 w-6 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(convo.id)
              }}
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        ))}
      </div>
    </ScrollArea>
  )
}