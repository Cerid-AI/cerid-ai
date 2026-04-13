// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Brain,
  Pencil,
  Trash2,
  RefreshCw,
  Lightbulb,
  ArrowRight,
  Bookmark,
  Loader2,
  X,
  Archive,
  Clock,
  MessageSquare,
  FolderKanban,
} from "lucide-react"
import { fetchMemories, updateMemory, deleteMemory, archiveMemories } from "@/lib/api"
import type { Memory } from "@/lib/types"
import { cn } from "@/lib/utils"

const MEMORY_TYPES = [
  { key: "empirical", label: "Empirical", icon: Lightbulb, bg: "bg-blue-500/10", text: "text-blue-500" },
  { key: "decision", label: "Decisions", icon: ArrowRight, bg: "bg-amber-500/10", text: "text-amber-500" },
  { key: "preference", label: "Preferences", icon: Bookmark, bg: "bg-green-500/10", text: "text-green-500" },
  { key: "project_context", label: "Project", icon: FolderKanban, bg: "bg-purple-500/10", text: "text-purple-500" },
  { key: "temporal", label: "Temporal", icon: Clock, bg: "bg-orange-500/10", text: "text-orange-500" },
  { key: "conversational", label: "Conversational", icon: MessageSquare, bg: "bg-cyan-500/10", text: "text-cyan-500" },
] as const

// Legacy type mapping for memories created before the 6-type classification
const LEGACY_TYPE_MAP: Record<string, string> = {
  fact: "empirical",
  action_item: "project_context",
}

type MemoryTypeKey = (typeof MEMORY_TYPES)[number]["key"]

function getTypeConfig(type: string) {
  const mapped = LEGACY_TYPE_MAP[type] ?? type
  return MEMORY_TYPES.find((t) => t.key === mapped) ?? MEMORY_TYPES[0]
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    })
  } catch {
    return iso
  }
}

function truncateId(id: string, len = 12): string {
  return id.length > len ? id.slice(0, len) + "..." : id
}

export default function MemoriesPane() {
  const [memories, setMemories] = useState<Memory[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<MemoryTypeKey | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState("")
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [archiving, setArchiving] = useState(false)
  const [archiveResult, setArchiveResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadMemories = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchMemories({ limit: 500 })
      setError(null)
      setMemories(res.memories)
    } catch (err) {
      console.error("Failed to load memories:", err)
      setError("Failed to load memories. Please try again.")
    } finally {
      setLoading(false)
    }
  }, [])

  const handleArchive = useCallback(async () => {
    setArchiving(true)
    setArchiveResult(null)
    try {
      const res = await archiveMemories(180)
      setArchiveResult(
        res.archived > 0
          ? `Archived ${res.archived} old memories`
          : "No memories older than 180 days",
      )
      if (res.archived > 0) loadMemories()
      setTimeout(() => setArchiveResult(null), 4000)
    } catch (err) {
      setArchiveResult(err instanceof Error ? err.message : "Archive failed")
      setTimeout(() => setArchiveResult(null), 4000)
    } finally {
      setArchiving(false)
    }
  }, [loadMemories])

  useEffect(() => {
    loadMemories()
  }, [loadMemories])

  const typeCounts = memories.reduce<Record<string, number>>((acc, m) => {
    const mapped = LEGACY_TYPE_MAP[m.type] ?? m.type
    acc[mapped] = (acc[mapped] ?? 0) + 1
    return acc
  }, {})

  const filtered = filter ? memories.filter((m) => (LEGACY_TYPE_MAP[m.type] ?? m.type) === filter) : memories

  const handleEdit = (memory: Memory) => {
    setEditingId(memory.id)
    setEditText(memory.content)
    setDeletingId(null)
  }

  const handleSave = async () => {
    if (!editingId || !editText.trim()) return
    setSaving(true)
    try {
      const updated = await updateMemory(editingId, editText.trim())
      setMemories((prev) =>
        prev.map((m) => (m.id === editingId ? { ...m, content: updated.content } : m)),
      )
      setEditingId(null)
      setEditText("")
    } catch (err) {
      console.error("Failed to update memory:", err)
    } finally {
      setSaving(false)
    }
  }

  const handleCancelEdit = () => {
    setEditingId(null)
    setEditText("")
  }

  const handleDelete = async (id: string) => {
    setSaving(true)
    try {
      await deleteMemory(id)
      setMemories((prev) => prev.filter((m) => m.id !== id))
      setDeletingId(null)
    } catch (err) {
      console.error("Failed to delete memory:", err)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Brain className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">Memories</h2>
            {!loading && (
              <span className="text-xs text-muted-foreground">({memories.length})</span>
            )}
          </div>
          <div className="flex gap-0.5">
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={handleArchive}
              disabled={loading || archiving}
              title="Archive memories older than 180 days"
            >
              {archiving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Archive className="h-3.5 w-3.5" />}
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={loadMemories}
              disabled={loading}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            </Button>
          </div>
        </div>

        {archiveResult && (
          <div className="mt-1 rounded bg-muted/50 px-2 py-1 text-[10px] text-muted-foreground">
            {archiveResult}
          </div>
        )}

        {/* Type filters */}
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Button
            variant="ghost"
            size="xs"
            className={cn("h-6", filter === null && "bg-muted font-medium")}
            onClick={() => setFilter(null)}
          >
            All
            <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
              {memories.length}
            </Badge>
          </Button>
          {MEMORY_TYPES.map((t) => {
            const count = typeCounts[t.key] ?? 0
            return (
              <Button
                key={t.key}
                variant="ghost"
                size="xs"
                className={cn("h-6", filter === t.key && "bg-muted font-medium")}
                onClick={() => setFilter(filter === t.key ? null : t.key)}
              >
                <t.icon className={cn("mr-0.5 h-3 w-3", t.text)} />
                {t.label}
                <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
                  {count}
                </Badge>
              </Button>
            )
          })}
        </div>
      </div>

      {/* Content */}
      {error && !loading ? (
        <div className="flex flex-col items-center gap-2 p-8 text-center text-sm text-muted-foreground">
          <p>{error}</p>
          <button onClick={loadMemories} className="text-xs underline hover:text-foreground">Retry</button>
        </div>
      ) : loading ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
          {[0, 1, 2].map((i) => (
            <div key={i} className="w-full max-w-md px-4">
              <div className="animate-pulse rounded-lg border bg-muted/30 p-4">
                <div className="mb-2 h-4 w-16 rounded bg-muted" />
                <div className="mb-1 h-3 w-full rounded bg-muted" />
                <div className="h-3 w-2/3 rounded bg-muted" />
              </div>
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
          <Brain className="h-10 w-10 opacity-30" />
          {filter ? (
            <p className="text-sm">No {getTypeConfig(filter).label.toLowerCase()} found.</p>
          ) : (
            <>
              <p className="text-sm font-medium">No memories extracted yet.</p>
              <p className="text-xs">
                Start a conversation to build your memory bank.
              </p>
            </>
          )}
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-2 p-4">
            {filtered.map((memory) => {
              const cfg = getTypeConfig(memory.type)
              const isEditing = editingId === memory.id
              const isDeleting = deletingId === memory.id

              return (
                <Card key={memory.id} className="overflow-hidden">
                  <CardContent className="min-w-0 p-3">
                    {/* Type badge + actions */}
                    <div className="flex items-start justify-between gap-2">
                      <Badge
                        variant="secondary"
                        className={cn("gap-1 text-[11px]", cfg.bg, cfg.text)}
                      >
                        <cfg.icon className="h-3 w-3" />
                        {cfg.label.replace(/s$/, "")}
                      </Badge>
                      <div className="flex gap-0.5">
                        {!isEditing && !isDeleting && (
                          <>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              onClick={() => handleEdit(memory)}
                              title="Edit"
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              className="text-destructive hover:text-destructive"
                              onClick={() => {
                                setDeletingId(memory.id)
                                setEditingId(null)
                              }}
                              title="Delete"
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </>
                        )}
                      </div>
                    </div>

                    {/* Content or edit mode */}
                    {isEditing ? (
                      <div className="mt-2 space-y-2">
                        <textarea
                          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                          rows={3}
                          value={editText}
                          onChange={(e) => setEditText(e.target.value)}
                          autoFocus
                        />
                        <div className="flex gap-1.5">
                          <Button
                            size="xs"
                            onClick={handleSave}
                            disabled={saving || !editText.trim()}
                          >
                            {saving && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                            Save
                          </Button>
                          <Button
                            variant="ghost"
                            size="xs"
                            onClick={handleCancelEdit}
                            disabled={saving}
                          >
                            Cancel
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <p className="mt-2 text-sm leading-relaxed [overflow-wrap:anywhere]">
                        {memory.content}
                      </p>
                    )}

                    {/* Delete confirmation */}
                    {isDeleting && (
                      <div className="mt-2 flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2">
                        <span className="flex-1 text-xs text-destructive">Are you sure?</span>
                        <Button
                          variant="destructive"
                          size="xs"
                          onClick={() => handleDelete(memory.id)}
                          disabled={saving}
                        >
                          {saving && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                          Delete
                        </Button>
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => setDeletingId(null)}
                          disabled={saving}
                        >
                          <X className="h-3 w-3" />
                        </Button>
                      </div>
                    )}

                    {/* Metadata footer */}
                    <div className="mt-2 flex items-center gap-2 text-[10px] text-muted-foreground">
                      <span title={memory.conversation_id}>
                        conv: {truncateId(memory.conversation_id)}
                      </span>
                      <span>{formatDate(memory.created_at)}</span>
                    </div>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </ScrollArea>
      )}
    </div>
  )
}