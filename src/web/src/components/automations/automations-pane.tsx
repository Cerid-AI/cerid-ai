// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import type { Automation, AutomationCreate } from "@/lib/types"
import {
  fetchAutomations,
  createAutomation,
  updateAutomation,
  deleteAutomation,
  toggleAutomation,
  runAutomation,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { EmptyState } from "@/components/ui/empty-state"
import AutomationDialog from "./automation-dialog"
import {
  Plus,
  Play,
  Pencil,
  Trash2,
  Loader2,
  AlertCircle,
  RefreshCw,
  Zap,
  Clock,
  Bell,
  BookOpen,
  Download,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert a 5-field cron expression to a human-readable string. */
function cronToHuman(cron: string): string {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return cron

  const [minute, hour, dayOfMonth, , dayOfWeek] = parts

  const h = parseInt(hour, 10)
  const m = parseInt(minute, 10)
  const timeStr =
    !isNaN(h) && !isNaN(m)
      ? `${h > 12 ? h - 12 : h || 12}:${String(m).padStart(2, "0")} ${h >= 12 ? "PM" : "AM"}`
      : `${hour}:${minute}`

  // Daily
  if (dayOfMonth === "*" && dayOfWeek === "*") return `Daily at ${timeStr}`

  // Weekdays
  if (dayOfMonth === "*" && dayOfWeek === "1-5") return `Weekdays at ${timeStr}`

  // Specific day of week
  const dayNames: Record<string, string> = {
    "0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday",
    "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday",
  }
  if (dayOfMonth === "*" && dayNames[dayOfWeek]) {
    return `Every ${dayNames[dayOfWeek]} at ${timeStr}`
  }

  // Monthly
  if (dayOfWeek === "*" && dayOfMonth !== "*") {
    const d = parseInt(dayOfMonth, 10)
    const suffix = d === 1 ? "st" : d === 2 ? "nd" : d === 3 ? "rd" : "th"
    return `Monthly on the ${d}${suffix} at ${timeStr}`
  }

  return cron
}

const ACTION_ICON: Record<string, typeof Bell> = {
  notify: Bell,
  digest: BookOpen,
  ingest: Download,
}

const ACTION_COLOR: Record<string, string> = {
  notify: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  digest: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
  ingest: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
}

const STATUS_DOT: Record<string, string> = {
  success: "bg-emerald-500",
  error: "bg-red-500",
  running: "bg-amber-500 animate-pulse",
}

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type LoadState = "loading" | "error" | "ready"

export default function AutomationsPane() {
  const [automations, setAutomations] = useState<Automation[]>([])
  const [loadState, setLoadState] = useState<LoadState>("loading")
  const [error, setError] = useState("")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<Automation | undefined>(undefined)
  const [saving, setSaving] = useState(false)
  const [runningId, setRunningId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setLoadState("loading")
      const data = await fetchAutomations()
      setAutomations(data)
      setLoadState("ready")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load automations")
      setLoadState("error")
    }
  }, [])

  useEffect(() => { load() }, [load])

  // --- Handlers ---

  async function handleSave(data: AutomationCreate) {
    setSaving(true)
    try {
      if (editTarget) {
        const updated = await updateAutomation(editTarget.id, data)
        setAutomations((prev) => prev.map((a) => (a.id === updated.id ? updated : a)))
      } else {
        const created = await createAutomation(data)
        setAutomations((prev) => [created, ...prev])
      }
      setDialogOpen(false)
      setEditTarget(undefined)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed")
    } finally {
      setSaving(false)
    }
  }

  async function handleToggle(id: string, enabled: boolean) {
    // Optimistic update
    setAutomations((prev) => prev.map((a) => (a.id === id ? { ...a, enabled } : a)))
    try {
      await toggleAutomation(id, enabled)
    } catch {
      // Revert on error
      setAutomations((prev) => prev.map((a) => (a.id === id ? { ...a, enabled: !enabled } : a)))
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id)
    try {
      await deleteAutomation(id)
      setAutomations((prev) => prev.filter((a) => a.id !== id))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setDeletingId(null)
    }
  }

  async function handleRunNow(id: string) {
    setRunningId(id)
    try {
      const run = await runAutomation(id)
      // Update last_run_at and last_status optimistically
      setAutomations((prev) =>
        prev.map((a) =>
          a.id === id
            ? { ...a, last_run_at: run.started_at, last_status: run.status, run_count: a.run_count + 1 }
            : a,
        ),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run failed")
    } finally {
      setRunningId(null)
    }
  }

  function openCreate() {
    setEditTarget(undefined)
    setDialogOpen(true)
  }

  function openEdit(automation: Automation) {
    setEditTarget(automation)
    setDialogOpen(true)
  }

  // --- Render ---

  if (loadState === "error") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-muted-foreground">{error || "Failed to load automations"}</p>
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw className="mr-2 h-3.5 w-3.5" />
          Retry
        </Button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-teal-500" />
          <h2 className="text-sm font-semibold">Automations</h2>
          {loadState === "ready" && automations.length > 0 && (
            <Badge variant="secondary" className="text-xs">
              {automations.length}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Button variant="ghost" size="icon" aria-label="Refresh automations" onClick={load} disabled={loadState === "loading"} className="h-7 w-7">
            <RefreshCw className={cn("h-3.5 w-3.5", loadState === "loading" && "animate-spin")} />
          </Button>
          <Button size="sm" onClick={openCreate} className="h-7 gap-1 px-2 text-xs">
            <Plus className="h-3.5 w-3.5" />
            New
          </Button>
        </div>
      </div>

      {/* Error banner */}
      {error && loadState === "ready" && (
        <div className="flex items-center gap-2 border-b border-destructive/30 bg-destructive/5 px-4 py-2">
          <AlertCircle className="h-3.5 w-3.5 text-destructive" />
          <p className="flex-1 text-xs text-destructive">{error}</p>
          <Button variant="ghost" size="sm" className="h-5 px-1 text-xs" onClick={() => setError("")}>
            Dismiss
          </Button>
        </div>
      )}

      {/* Content */}
      <ScrollArea className="flex-1">
        <div className="p-4">
          {loadState === "loading" ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : automations.length === 0 ? (
            <EmptyState
              icon={Zap}
              title="No automations yet"
              description="Create one to schedule recurring knowledge tasks."
            />
          ) : (
            <div className="space-y-3">
              {automations.map((auto) => {
                const ActionIcon = ACTION_ICON[auto.action] ?? Bell
                const isRunning = runningId === auto.id
                const isDeleting = deletingId === auto.id

                return (
                  <Card
                    key={auto.id}
                    className={cn(
                      "transition-opacity",
                      !auto.enabled && "opacity-60",
                      isDeleting && "pointer-events-none opacity-40",
                    )}
                  >
                    <CardContent className="px-4 py-3">
                      {/* Top row: name + toggle */}
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <h3 className="truncate text-sm font-medium">{auto.name}</h3>
                            {auto.last_status && (
                              <span
                                className={cn(
                                  "inline-block h-2 w-2 shrink-0 rounded-full",
                                  STATUS_DOT[auto.last_status] ?? "bg-muted-foreground",
                                )}
                                title={auto.last_status}
                              />
                            )}
                          </div>
                          {auto.description && (
                            <p className="mt-0.5 truncate text-xs text-muted-foreground">
                              {auto.description}
                            </p>
                          )}
                        </div>
                        <Switch
                          checked={auto.enabled}
                          onCheckedChange={(v) => handleToggle(auto.id, v)}
                          className="shrink-0"
                        />
                      </div>

                      {/* Meta row: schedule + action badge + last run */}
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {cronToHuman(auto.schedule)}
                        </span>
                        <Badge
                          variant="secondary"
                          className={cn("gap-1 text-[10px] font-medium", ACTION_COLOR[auto.action])}
                        >
                          <ActionIcon className="h-2.5 w-2.5" />
                          {auto.action.charAt(0).toUpperCase() + auto.action.slice(1)}
                        </Badge>
                        {auto.last_run_at && (
                          <span className="ml-auto text-[10px]">
                            Last run {formatRelativeTime(auto.last_run_at)}
                          </span>
                        )}
                      </div>

                      {/* Domain pills */}
                      {auto.domains.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {auto.domains.map((d) => (
                            <span
                              key={d}
                              className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                            >
                              {d}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Actions row */}
                      <div className="mt-2 flex items-center gap-1 border-t border-border pt-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 gap-1 px-2 text-xs"
                          onClick={() => handleRunNow(auto.id)}
                          disabled={isRunning || !auto.enabled}
                        >
                          {isRunning ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Play className="h-3 w-3" />
                          )}
                          Run Now
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 gap-1 px-2 text-xs"
                          onClick={() => openEdit(auto)}
                        >
                          <Pencil className="h-3 w-3" />
                          Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 gap-1 px-2 text-xs text-destructive hover:text-destructive"
                          onClick={() => handleDelete(auto.id)}
                          disabled={isDeleting}
                        >
                          {isDeleting ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Trash2 className="h-3 w-3" />
                          )}
                          Delete
                        </Button>
                        {auto.run_count > 0 && (
                          <span className="ml-auto text-[10px] text-muted-foreground">
                            {auto.run_count} run{auto.run_count !== 1 ? "s" : ""}
                          </span>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                )
              })}
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Create / Edit dialog */}
      <AutomationDialog
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditTarget(undefined) }}
        automation={editTarget}
        onSave={handleSave}
        saving={saving}
      />
    </div>
  )
}
