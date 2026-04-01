// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useEffect, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Archive, ArchiveRestore, Search, Trash2, Pencil, X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Conversation } from "@/lib/types"

/** Highlight matching substrings in text by wrapping them in <mark> tags. */
function HighlightedText({ text, query }: { text: string; query: string }): ReactNode {
  if (!query) return text
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const parts = text.split(new RegExp(`(${escaped})`, "gi"))
  if (parts.length === 1) return text
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === query.toLowerCase() ? (
          <mark key={i} className="bg-brand/25 text-foreground rounded-sm px-0.5">{part}</mark>
        ) : (
          part
        )
      )}
    </>
  )
}

interface ConversationListProps {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onArchive: (id: string) => void
  onUnarchive: (id: string) => void
  showArchived: boolean
  archivedCount: number
  onToggleShowArchived: () => void
  onBulkDelete: (ids: string[]) => void
  onBulkArchive: (ids: string[]) => void
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onDelete,
  onArchive,
  onUnarchive,
  showArchived,
  archivedCount,
  onToggleShowArchived,
  onBulkDelete,
  onBulkArchive,
}: ConversationListProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [editMode, setEditMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Debounce search input by 300ms
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setDebouncedQuery(searchQuery.trim().toLowerCase())
    }, 300)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [searchQuery])

  // Filter conversations by search query (title + message content)
  const filtered = debouncedQuery
    ? conversations.filter((c) => {
        if (c.title.toLowerCase().includes(debouncedQuery)) return true
        return c.messages.some((m) => m.content.toLowerCase().includes(debouncedQuery))
      })
    : conversations

  // Clear selection when exiting edit mode or switching archive view
  const exitEditMode = useCallback(() => {
    setEditMode(false)
    setSelectedIds(new Set())
  }, [])

  useEffect(() => {
    exitEditMode()
  }, [showArchived, exitEditMode])

  const toggleSelected = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      if (prev.size === filtered.length) return new Set()
      return new Set(filtered.map((c) => c.id))
    })
  }, [filtered])

  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)

  const handleBulkDelete = useCallback(() => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    onBulkDelete(ids)
    setConfirmDeleteOpen(false)
    exitEditMode()
  }, [selectedIds, onBulkDelete, exitEditMode])

  const handleBulkArchive = useCallback(() => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    onBulkArchive(ids)
    exitEditMode()
  }, [selectedIds, onBulkArchive, exitEditMode])

  return (
    <div className="flex h-full flex-col">
      {/* Search bar */}
      <div className="flex items-center gap-1 border-b px-2 py-2">
        <div className="relative flex-1">
          <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={cn("h-8 pl-7 text-sm", searchQuery && "pr-7")}
          />
          {searchQuery && (
            <button
              type="button"
              aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setSearchQuery("")}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        {!editMode ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Edit conversations"
            className="h-8 w-8 flex-shrink-0"
            onClick={() => setEditMode(true)}
            disabled={filtered.length === 0}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Cancel editing"
            className="h-8 w-8 flex-shrink-0"
            onClick={exitEditMode}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      {/* Bulk action bar (visible in edit mode) */}
      {editMode && (
        <div className="flex items-center gap-1 border-b px-2 py-1.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={toggleSelectAll}
          >
            {selectedIds.size === filtered.length && filtered.length > 0
              ? "Deselect all"
              : "Select all"}
          </Button>
          <div className="flex-1" />
          {!showArchived && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-xs"
              disabled={selectedIds.size === 0}
              onClick={handleBulkArchive}
            >
              <Archive className="h-3 w-3" />
              Archive ({selectedIds.size})
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs text-destructive hover:text-destructive"
            disabled={selectedIds.size === 0}
            onClick={() => setConfirmDeleteOpen(true)}
          >
            <Trash2 className="h-3 w-3" />
            Delete ({selectedIds.size})
          </Button>
        </div>
      )}

      {/* Conversation list */}
      {filtered.length === 0 ? (
        <div className="p-4 text-center text-sm text-muted-foreground">
          {debouncedQuery
            ? "No matching conversations"
            : showArchived
              ? "No archived conversations"
              : "No conversations yet"}
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="space-y-1 p-2">
            {filtered.map((convo) => (
              <div
                key={convo.id}
                role="button"
                tabIndex={0}
                className={cn(
                  "group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-muted",
                  activeId === convo.id && "bg-muted",
                )}
                onClick={() => {
                  if (editMode) {
                    toggleSelected(convo.id)
                  } else {
                    onSelect(convo.id)
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    if (editMode) {
                      toggleSelected(convo.id)
                    } else {
                      onSelect(convo.id)
                    }
                  }
                }}
              >
                {editMode && (
                  <Checkbox
                    checked={selectedIds.has(convo.id)}
                    onCheckedChange={() => toggleSelected(convo.id)}
                    onClick={(e) => e.stopPropagation()}
                    className="flex-shrink-0"
                    aria-label={`Select ${convo.title}`}
                  />
                )}
                <span className="min-w-0 flex-1 truncate">
                  <HighlightedText text={convo.title} query={debouncedQuery} />
                </span>
                {!editMode && (
                  <div className="flex flex-shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 [@media(pointer:coarse)]:opacity-60">
                    {showArchived ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Unarchive conversation"
                        className="h-7 w-7"
                        onClick={(e) => {
                          e.stopPropagation()
                          onUnarchive(convo.id)
                        }}
                      >
                        <ArchiveRestore className="h-3 w-3" />
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Archive conversation"
                        className="h-7 w-7"
                        onClick={(e) => {
                          e.stopPropagation()
                          onArchive(convo.id)
                        }}
                      >
                        <Archive className="h-3 w-3" />
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Delete conversation"
                      className="h-7 w-7"
                      onClick={(e) => {
                        e.stopPropagation()
                        onDelete(convo.id)
                      }}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </ScrollArea>
      )}

      {/* Archive toggle footer */}
      <div className="border-t px-2 py-1.5">
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-center gap-1.5 text-xs text-muted-foreground"
          onClick={onToggleShowArchived}
        >
          <Archive className="h-3 w-3" />
          {showArchived
            ? "Back to active"
            : `View archived (${archivedCount})`}
        </Button>
      </div>

      {/* Bulk delete confirmation dialog */}
      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {selectedIds.size} conversation{selectedIds.size !== 1 ? "s" : ""}?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. The selected conversation{selectedIds.size !== 1 ? "s" : ""} will be permanently deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleBulkDelete}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
