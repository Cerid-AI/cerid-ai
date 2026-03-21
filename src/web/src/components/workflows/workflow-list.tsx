// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import type { Workflow, WorkflowRun } from "@/lib/types"
import {
  fetchWorkflows,
  deleteWorkflow,
  fetchWorkflowRuns,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import {
  Plus,
  Pencil,
  Trash2,
  Copy,
  Loader2,
  AlertCircle,
  RefreshCw,
  GitBranch,
  ChevronDown,
  ChevronRight,
  Clock,
  CheckCircle2,
  XCircle,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WorkflowListProps {
  onEdit: (workflow: Workflow) => void
  onCreate: () => void
  onDuplicate: (workflow: Workflow) => void
}

// ---------------------------------------------------------------------------
// Run status badge
// ---------------------------------------------------------------------------

function RunStatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; className: string; icon: React.ReactNode }> = {
    completed: {
      label: "Completed",
      className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
      icon: <CheckCircle2 className="h-3 w-3" />,
    },
    failed: {
      label: "Failed",
      className: "bg-red-500/15 text-red-400 border-red-500/30",
      icon: <XCircle className="h-3 w-3" />,
    },
    running: {
      label: "Running",
      className: "bg-teal-500/15 text-teal-400 border-teal-500/30 animate-pulse",
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
    },
    pending: {
      label: "Pending",
      className: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
      icon: <Clock className="h-3 w-3" />,
    },
  }
  const s = map[status] ?? map.pending
  return (
    <span className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border", s.className)}>
      {s.icon} {s.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// WorkflowList
// ---------------------------------------------------------------------------

export default function WorkflowList({ onEdit, onCreate, onDuplicate }: WorkflowListProps) {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedRuns, setExpandedRuns] = useState<Record<string, WorkflowRun[]>>({})
  const [loadingRuns, setLoadingRuns] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await fetchWorkflows()
      setWorkflows(resp.workflows)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load workflows")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleDelete = useCallback(async (wf: Workflow) => {
    setDeleting(wf.id)
    try {
      await deleteWorkflow(wf.id)
      setWorkflows((prev) => prev.filter((w) => w.id !== wf.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed")
    } finally {
      setDeleting(null)
    }
  }, [])

  const toggleRuns = useCallback(async (wfId: string) => {
    if (expandedRuns[wfId]) {
      setExpandedRuns((prev) => {
        const next = { ...prev }
        delete next[wfId]
        return next
      })
      return
    }
    setLoadingRuns(wfId)
    try {
      const runs = await fetchWorkflowRuns(wfId, 5)
      setExpandedRuns((prev) => ({ ...prev, [wfId]: runs }))
    } catch {
      // silently fail
    } finally {
      setLoadingRuns(null)
    }
  }, [expandedRuns])

  // ── Loading / Error / Empty ────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-zinc-500">
        <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading workflows...
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-3">
        <AlertCircle className="h-6 w-6 text-red-400" />
        <p className="text-sm text-red-400">{error}</p>
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" /> Retry
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-zinc-800">
        <h2 className="text-sm font-semibold text-zinc-200 flex items-center gap-1.5">
          <GitBranch className="h-4 w-4 text-teal-400" />
          Workflows
          <Badge variant="outline" className="ml-1.5 text-[10px]">{workflows.length}</Badge>
        </h2>
        <div className="flex gap-1.5">
          <Button variant="ghost" size="sm" aria-label="Refresh workflows" onClick={load}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" onClick={onCreate}>
            <Plus className="h-3.5 w-3.5 mr-1" /> New Workflow
          </Button>
        </div>
      </div>

      {workflows.length === 0 ? (
        <EmptyState
          icon={GitBranch}
          title="No workflows yet"
          description="Create a workflow to visually compose agent pipelines"
        />
      ) : (
        <ScrollArea className="flex-1">
          <div className="p-3 space-y-2">
            {workflows.map((wf) => (
              <Card key={wf.id} className="bg-zinc-900/60 border-zinc-800 hover:border-zinc-700 transition-colors">
                <CardContent className="p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <button
                        className="text-sm font-medium text-zinc-100 hover:text-teal-300 transition-colors text-left truncate block w-full"
                        onClick={() => onEdit(wf)}
                      >
                        {wf.name}
                      </button>
                      {wf.description && (
                        <p className="text-xs text-zinc-500 mt-0.5 truncate">{wf.description}</p>
                      )}
                      <div className="flex items-center gap-2 mt-1.5">
                        <Badge variant="outline" className="text-[10px]">
                          {wf.nodes.length} nodes
                        </Badge>
                        <Badge variant="outline" className="text-[10px]">
                          {wf.edges.length} edges
                        </Badge>
                        {!wf.enabled && (
                          <Badge variant="destructive" className="text-[10px]">disabled</Badge>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" aria-label="Edit workflow" onClick={() => onEdit(wf)}>
                        <Pencil className="h-3.5 w-3.5 text-zinc-400" />
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" aria-label="Duplicate workflow" onClick={() => onDuplicate(wf)}>
                        <Copy className="h-3.5 w-3.5 text-zinc-400" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        aria-label="Delete workflow"
                        onClick={() => handleDelete(wf)}
                        disabled={deleting === wf.id}
                      >
                        {deleting === wf.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-zinc-400" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5 text-zinc-400 hover:text-red-400" />
                        )}
                      </Button>
                    </div>
                  </div>

                  {/* Expandable run history */}
                  <button
                    className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-300 mt-2"
                    onClick={() => toggleRuns(wf.id)}
                  >
                    {expandedRuns[wf.id] ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    {loadingRuns === wf.id ? "Loading..." : "Run History"}
                  </button>

                  {expandedRuns[wf.id] && (
                    <div className="mt-1.5 space-y-1 pl-3 border-l border-zinc-800">
                      {expandedRuns[wf.id].length === 0 ? (
                        <p className="text-[10px] text-zinc-600">No runs yet</p>
                      ) : (
                        expandedRuns[wf.id].map((run) => (
                          <div key={run.id} className="flex items-center gap-2 text-[10px] text-zinc-500">
                            <RunStatusBadge status={run.status} />
                            <span className="font-mono">{run.id.slice(0, 8)}</span>
                            <span>{new Date(run.started_at).toLocaleString()}</span>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  )
}
