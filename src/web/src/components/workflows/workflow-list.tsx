// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useCallback, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import type { Workflow, WorkflowRun } from "@/lib/types"
import {
  fetchWorkflows,
  deleteWorkflow,
  fetchWorkflowRuns,
  runWorkflow,
} from "@/lib/api"
import { consumesQueryInput } from "./node-catalog"
import { cn } from "@/lib/utils"
import { logSwallowedError } from "@/lib/log-swallowed"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { Input } from "@/components/ui/input"
import {
  Plus,
  Pencil,
  Trash2,
  Copy,
  Loader2,
  RefreshCw,
  GitBranch,
  ChevronDown,
  ChevronRight,
  Clock,
  CheckCircle2,
  XCircle,
  Play,
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
      className: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
      icon: <XCircle className="h-3 w-3" />,
    },
    running: {
      label: "Running",
      className: "bg-teal-500/15 text-teal-400 border-teal-500/30 animate-pulse",
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
    },
    pending: {
      label: "Pending",
      className: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30",
      icon: <Clock className="h-3 w-3" />,
    },
  }
  const s = map[status] ?? map.pending
  return (
    <span className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-label-xs font-medium border", s.className)}>
      {s.icon} {s.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// WorkflowList
// ---------------------------------------------------------------------------

export default function WorkflowList({ onEdit, onCreate, onDuplicate }: WorkflowListProps) {
  const queryClient = useQueryClient()
  const {
    data: workflows = [],
    isLoading: loading,
    isError,
    error: queryError,
    refetch,
  } = useQuery({
    queryKey: ["workflows"],
    queryFn: async () => {
      const resp = await fetchWorkflows()
      return Array.isArray(resp.workflows) ? resp.workflows : []
    },
  })
  const error = isError
    ? (queryError instanceof Error ? queryError.message : "Failed to load workflows")
    : null
  const load = useCallback(() => {
    void refetch()
  }, [refetch])

  const [expandedRuns, setExpandedRuns] = useState<Record<string, WorkflowRun[]>>({})
  const [loadingRuns, setLoadingRuns] = useState<string | null>(null)
  const [runsError, setRunsError] = useState<Record<string, string>>({})
  const [deleting, setDeleting] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  // UX-21: Run straight from the card — no editor round-trip. `runFor` is
  // the card whose run-input row is open; a query-consuming pipeline asks
  // for its input here instead of running on a made-up one.
  const [runFor, setRunFor] = useState<string | null>(null)
  const [runInput, setRunInput] = useState("")
  const [runningId, setRunningId] = useState<string | null>(null)
  const [lastRun, setLastRun] = useState<Record<string, WorkflowRun>>({})

  const startRun = useCallback(async (wf: Workflow) => {
    if (consumesQueryInput(wf.nodes) && !runInput.trim()) {
      setRunsError((prev) => ({ ...prev, [wf.id]: "This pipeline takes a query — enter it first" }))
      return
    }
    setRunningId(wf.id)
    setRunsError((prev) => {
      const next = { ...prev }
      delete next[wf.id]
      return next
    })
    try {
      const run = await runWorkflow(wf.id, runInput.trim() ? { query: runInput.trim() } : {})
      setLastRun((prev) => ({ ...prev, [wf.id]: run }))
      setRunFor(null)
      setRunInput("")
    } catch (e) {
      logSwallowedError(e, "workflow-list.runWorkflow", { workflowId: wf.id })
      setRunsError((prev) => ({ ...prev, [wf.id]: e instanceof Error ? e.message : "Run failed" }))
    } finally {
      setRunningId(null)
    }
  }, [runInput])

  const handleDelete = useCallback(async (wf: Workflow) => {
    setDeleting(wf.id)
    setDeleteError(null)
    try {
      await deleteWorkflow(wf.id)
      await queryClient.invalidateQueries({ queryKey: ["workflows"] })
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Delete failed")
    } finally {
      setDeleting(null)
    }
  }, [queryClient])

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
    setRunsError((prev) => {
      const next = { ...prev }
      delete next[wfId]
      return next
    })
    try {
      const runs = await fetchWorkflowRuns(wfId, 5)
      setExpandedRuns((prev) => ({ ...prev, [wfId]: runs }))
    } catch (e) {
      logSwallowedError(e, "workflow-list.fetchWorkflowRuns", { workflowId: wfId })
      setRunsError((prev) => ({ ...prev, [wfId]: e instanceof Error ? e.message : "Failed to load run history" }))
    } finally {
      setLoadingRuns(null)
    }
  }, [expandedRuns])

  // ── Loading / Error / Empty ────────────────────────────────────────

  if (loading) {
    return (
      <div className="space-y-2 p-3" role="status" aria-label="Loading workflows">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full rounded-lg" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-3">
        <PaneError title="Failed to load workflows" description={error} onRetry={() => void load()} />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border">
        <h2 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
          <GitBranch className="h-4 w-4 text-teal-400" />
          Workflows
          <Badge variant="outline" className="ml-1.5 text-label-xs">{workflows.length}</Badge>
        </h2>
        <div className="flex gap-1.5">
          <Button variant="ghost" size="sm" aria-label="Refresh workflows" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" onClick={onCreate}>
            <Plus className="h-3.5 w-3.5 mr-1" /> New Workflow
          </Button>
        </div>
      </div>
      {deleteError ? (
        <div className="px-3 pt-2">
          <PaneError title="Delete failed" description={deleteError} onRetry={() => setDeleteError(null)} />
        </div>
      ) : null}

      {workflows.length === 0 ? (
        <div className="p-3 space-y-3 max-w-xl mx-auto w-full">
          <EmptyState
            icon={GitBranch}
            title="No workflows yet"
            description="A workflow chains Cerid's agents into a repeatable pipeline — retrieval, verification, curation — that runs against your knowledge base on demand."
          />
          <ol className="space-y-2 rounded-lg border bg-muted/30 p-3" aria-label="How to build a workflow">
            {[
              { step: "Create", detail: "Start from a template or a blank canvas." },
              { step: "Compose", detail: "Add agent, parser, tool, and condition nodes, then connect them with edges to set the execution order." },
              { step: "Run", detail: "Save, execute the pipeline, and review each node's result." },
            ].map((item, i) => (
              <li key={item.step} className="flex items-start gap-2.5 text-xs">
                <span
                  aria-hidden="true"
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-teal-500/40 bg-teal-500/10 text-label-xs font-semibold text-teal-600 dark:text-teal-300"
                >
                  {i + 1}
                </span>
                <span className="pt-0.5">
                  <span className="font-medium text-foreground">{item.step}</span>
                  <span className="text-muted-foreground"> — {item.detail}</span>
                </span>
              </li>
            ))}
          </ol>
          <div className="flex justify-center">
            <Button size="sm" onClick={onCreate}>
              <Plus className="h-3.5 w-3.5 mr-1" /> Create your first workflow
            </Button>
          </div>
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="p-3 space-y-2">
            {workflows.map((wf) => (
              <Card key={wf.id} className="bg-card border-border hover:border-muted-foreground/40 transition-colors">
                <CardContent className="p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <button
                        className="text-sm font-medium text-foreground hover:text-teal-300 transition-colors text-left truncate block w-full"
                        onClick={() => onEdit(wf)}
                      >
                        {wf.name}
                      </button>
                      {wf.description && (
                        <p className="text-xs text-muted-foreground mt-0.5 truncate">{wf.description}</p>
                      )}
                      <div className="flex items-center gap-2 mt-1.5">
                        <Badge variant="outline" className="text-label-xs">
                          {wf.nodes.length} nodes
                        </Badge>
                        <Badge variant="outline" className="text-label-xs">
                          {wf.edges.length} edges
                        </Badge>
                        {!wf.enabled && (
                          <Badge variant="destructive" className="text-label-xs">disabled</Badge>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        aria-label="Run workflow"
                        onClick={() => {
                          setRunFor((prev) => (prev === wf.id ? null : wf.id))
                          setRunInput("")
                        }}
                        disabled={runningId === wf.id || !wf.enabled}
                      >
                        {runningId === wf.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                        ) : (
                          <Play className="h-3.5 w-3.5 text-muted-foreground" />
                        )}
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" aria-label="Edit workflow" onClick={() => onEdit(wf)}>
                        <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" aria-label="Duplicate workflow" onClick={() => onDuplicate(wf)}>
                        <Copy className="h-3.5 w-3.5 text-muted-foreground" />
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
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-red-700 dark:text-red-400" />
                        )}
                      </Button>
                    </div>
                  </div>

                  {/* UX-21: inline run-input row (opened by the card's Run) */}
                  {runFor === wf.id && (
                    <div className="mt-2 flex items-center gap-1.5">
                      {consumesQueryInput(wf.nodes) && (
                        <Input
                          value={runInput}
                          onChange={(e) => setRunInput(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") void startRun(wf) }}
                          placeholder="Query to run on..."
                          aria-label="Query input for this run"
                          className="h-7 flex-1 text-xs"
                          // eslint-disable-next-line jsx-a11y/no-autofocus -- user-triggered inline input; mount means the card's Run was explicitly clicked
                          autoFocus
                        />
                      )}
                      <Button
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => void startRun(wf)}
                        disabled={runningId === wf.id}
                      >
                        {runningId === wf.id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Start run"}
                      </Button>
                    </div>
                  )}

                  {/* Freshest run fired from this card */}
                  {lastRun[wf.id] && (
                    <div className="mt-2 flex items-center gap-2 text-label-xs text-muted-foreground">
                      <RunStatusBadge status={lastRun[wf.id].status} />
                      <span className="font-mono">{lastRun[wf.id].id.slice(0, 8)}</span>
                    </div>
                  )}

                  {/* Expandable run history */}
                  <button
                    className="flex items-center gap-1 text-label-xs text-muted-foreground hover:text-foreground mt-2"
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
                    <div className="mt-1.5 space-y-1 pl-3 border-l border-border">
                      {expandedRuns[wf.id].length === 0 ? (
                        <p className="text-label-xs text-muted-foreground/70">No runs yet</p>
                      ) : (
                        expandedRuns[wf.id].map((run) => (
                          <div key={run.id} className="flex items-center gap-2 text-label-xs text-muted-foreground">
                            <RunStatusBadge status={run.status} />
                            <span className="font-mono">{run.id.slice(0, 8)}</span>
                            <span>{new Date(run.started_at).toLocaleString()}</span>
                          </div>
                        ))
                      )}
                    </div>
                  )}

                  {runsError[wf.id] && (
                    <p className="mt-1.5 pl-3 text-label-xs text-destructive">{runsError[wf.id]}</p>
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
