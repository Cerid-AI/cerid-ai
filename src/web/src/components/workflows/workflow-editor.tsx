// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import type {
  Workflow,
  WorkflowCreate,
  WorkflowNode,
  WorkflowEdge,
  WorkflowNodeType,
  WorkflowRun,
  WorkflowTemplate,
} from "@/lib/types"
import {
  createWorkflow,
  updateWorkflow,
  runWorkflow,
  fetchWorkflowTemplates,
} from "@/lib/api"
import { logSwallowedError } from "@/lib/log-swallowed"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"
import WorkflowCanvas from "./workflow-canvas"
import {
  Plus,
  Trash2,
  Save,
  Play,
  Loader2,
  ArrowRight,
  ChevronDown,
  X,
  Settings2,
  AlertCircle,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Available node types for the palette
// ---------------------------------------------------------------------------

const AGENT_NAMES = [
  "query", "curator", "triage", "rectify", "audit",
  "maintenance", "hallucination", "memory", "self_rag",
]

const NODE_TYPE_OPTIONS: { type: WorkflowNodeType; label: string }[] = [
  { type: "agent", label: "Agent" },
  { type: "parser", label: "Parser" },
  { type: "tool", label: "Tool" },
  { type: "condition", label: "Condition" },
]

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WorkflowEditorProps {
  workflow: Workflow | null
  onSave: (workflow: Workflow) => void
  onBack: () => void
}

// ---------------------------------------------------------------------------
// WorkflowEditor
// ---------------------------------------------------------------------------

export default function WorkflowEditor({ workflow, onSave, onBack }: WorkflowEditorProps) {
  const [name, setName] = useState(workflow?.name ?? "")
  const [description, setDescription] = useState(workflow?.description ?? "")
  const [nodes, setNodes] = useState<WorkflowNode[]>(workflow?.nodes ?? [])
  const [edges, setEdges] = useState<WorkflowEdge[]>(workflow?.edges ?? [])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<WorkflowRun | null>(null)
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, "pending" | "running" | "completed" | "failed">>({})
  const [edgeMode, setEdgeMode] = useState<string | null>(null) // source_id when connecting
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([])
  const [templatesError, setTemplatesError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Load templates on mount
  useEffect(() => {
    fetchWorkflowTemplates()
      .then(setTemplates)
      .catch((e) => {
        logSwallowedError(e, "workflow-editor.fetchWorkflowTemplates")
        setTemplatesError(e instanceof Error ? e.message : "Failed to load templates")
      })
  }, [])

  // Reset state when workflow changes
  useEffect(() => {
    if (workflow) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setName(workflow.name)
      setDescription(workflow.description)
      setNodes(workflow.nodes)
      setEdges(workflow.edges)
    }
  }, [workflow])

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null

  // ── Node operations ──────────────────────────────────────────────────

  const addNode = useCallback((type: WorkflowNodeType, nodeName: string) => {
    const id = `${nodeName}_${Date.now().toString(36)}`
    const maxX = Math.max(0, ...nodes.map((n) => n.position.x))
    const newNode: WorkflowNode = {
      id,
      type,
      name: nodeName,
      config: type === "condition" ? { expression: "confidence > 0.5" } : {},
      position: { x: maxX + 220, y: 200 },
    }
    setNodes((prev) => [...prev, newNode])
    setSelectedNodeId(id)
  }, [nodes])

  const deleteSelected = useCallback(() => {
    if (!selectedNodeId) return
    setNodes((prev) => prev.filter((n) => n.id !== selectedNodeId))
    setEdges((prev) => prev.filter((e) => e.source_id !== selectedNodeId && e.target_id !== selectedNodeId))
    setSelectedNodeId(null)
  }, [selectedNodeId])

  const handleNodeMove = useCallback((nodeId: string, x: number, y: number) => {
    setNodes((prev) => prev.map((n) => (n.id === nodeId ? { ...n, position: { x, y } } : n)))
  }, [])

  const handleNodeClick = useCallback((nodeId: string) => {
    if (edgeMode) {
      // Complete edge creation
      if (edgeMode !== nodeId) {
        setEdges((prev) => [...prev, { source_id: edgeMode, target_id: nodeId, label: null, condition: null }])
      }
      setEdgeMode(null)
    } else {
      setSelectedNodeId(nodeId)
    }
  }, [edgeMode])

  const updateNodeConfig = useCallback((key: string, value: string) => {
    if (!selectedNodeId) return
    setNodes((prev) =>
      prev.map((n) =>
        n.id === selectedNodeId
          ? { ...n, config: { ...n.config, [key]: value } }
          : n,
      ),
    )
  }, [selectedNodeId])

  const updateNodeName = useCallback((newName: string) => {
    if (!selectedNodeId) return
    setNodes((prev) =>
      prev.map((n) => (n.id === selectedNodeId ? { ...n, name: newName } : n)),
    )
  }, [selectedNodeId])

  // ── Template application ───────────────────────────────────────────────

  const applyTemplate = useCallback((template: WorkflowTemplate) => {
    setName(template.name)
    setDescription(template.description)
    setNodes(template.nodes)
    setEdges(template.edges)
    setSelectedNodeId(null)
  }, [])

  // ── Save ───────────────────────────────────────────────────────────────

  const handleSave = useCallback(async () => {
    if (!name.trim()) {
      setError("Workflow name is required")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const data: WorkflowCreate = { name, description, nodes, edges, enabled: true }
      const saved = workflow?.id
        ? await updateWorkflow(workflow.id, data)
        : await createWorkflow(data)
      onSave(saved)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed")
    } finally {
      setSaving(false)
    }
  }, [name, description, nodes, edges, workflow, onSave])

  // ── Run ────────────────────────────────────────────────────────────────

  const handleRun = useCallback(async () => {
    if (!workflow?.id) {
      setError("Save the workflow before running")
      return
    }
    setRunning(true)
    setRunResult(null)
    setError(null)

    // Set all nodes to pending, then running
    const pendingStatuses: Record<string, "pending"> = {}
    for (const n of nodes) pendingStatuses[n.id] = "pending"
    setNodeStatuses(pendingStatuses)

    try {
      const result = await runWorkflow(workflow.id, { query: "test" })
      setRunResult(result)

      // Map result statuses to node statuses
      const statuses: Record<string, "pending" | "running" | "completed" | "failed"> = {}
      for (const [nodeId, nodeResult] of Object.entries(result.results)) {
        const r = nodeResult as Record<string, unknown>
        if (r.type === "skipped") statuses[nodeId] = "pending"
        else if (r.status === "completed") statuses[nodeId] = "completed"
        else statuses[nodeId] = "failed"
      }
      setNodeStatuses(statuses)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed")
      const failedStatuses: Record<string, "failed"> = {}
      for (const n of nodes) failedStatuses[n.id] = "failed"
      setNodeStatuses(failedStatuses)
    } finally {
      setRunning(false)
    }
  }, [workflow, nodes])

  return (
    <div className="flex flex-col h-full">
      {/* ── Top toolbar ──────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 p-3 border-b bg-muted/40 flex-wrap">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <X className="h-4 w-4 mr-1" /> Back
        </Button>

        <div className="h-4 w-px bg-border" />

        {/* Template selector */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              Templates <ChevronDown className="h-3 w-3 ml-1" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-[260px]"> {/* drift-allowed: template dropdown pinned width fits longest template name */}
            {templates.length === 0 ? (
              <div className="px-2 py-1.5 text-sm text-muted-foreground">No templates available</div>
            ) : (
              templates.map((t) => (
                <DropdownMenuItem key={t.id} onSelect={() => applyTemplate(t)} className="flex-col items-start gap-0.5">
                  <span className="font-medium text-foreground">{t.name}</span>
                  <span className="text-xs text-muted-foreground">{t.description}</span>
                </DropdownMenuItem>
              ))
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        {templatesError && (
          <span className="text-label-xs text-muted-foreground">Templates unavailable</span>
        )}

        <div className="h-4 w-px bg-border" />

        {/* Add node */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              <Plus className="h-3.5 w-3.5 mr-1" /> Add Node
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-[200px] max-h-[300px] overflow-y-auto"> {/* drift-allowed: node dropdown pinned width fits longest node name */}
            <DropdownMenuLabel>Agents</DropdownMenuLabel>
            {AGENT_NAMES.map((a) => (
              <DropdownMenuItem key={a} onSelect={() => addNode("agent", a)}>
                {a}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Other</DropdownMenuLabel>
            {NODE_TYPE_OPTIONS.filter((o) => o.type !== "agent").map((o) => (
              <DropdownMenuItem key={o.type} onSelect={() => addNode(o.type, o.label.toLowerCase())}>
                {o.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Add edge mode */}
        <Button
          variant={edgeMode ? "default" : "outline"}
          size="sm"
          onClick={() => {
            if (edgeMode) {
              setEdgeMode(null)
            } else if (selectedNodeId) {
              setEdgeMode(selectedNodeId)
            }
          }}
          disabled={!selectedNodeId && !edgeMode}
        >
          <ArrowRight className="h-3.5 w-3.5 mr-1" />
          {edgeMode ? "Click target..." : "Add Edge"}
        </Button>

        {/* Delete */}
        <Button
          variant="outline"
          size="sm"
          onClick={deleteSelected}
          disabled={!selectedNodeId}
        >
          <Trash2 className="h-3.5 w-3.5 mr-1" /> Delete
        </Button>

        <div className="flex-1" />

        {/* Run + Save */}
        <Button
          variant="outline"
          size="sm"
          onClick={handleRun}
          disabled={running || !workflow?.id || nodes.length === 0}
        >
          {running ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Play className="h-3.5 w-3.5 mr-1" />}
          Run
        </Button>
        <Button size="sm" onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Save className="h-3.5 w-3.5 mr-1" />}
          Save
        </Button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="px-3 pt-2">
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            <AlertDescription className="flex items-center justify-between gap-2">
              <span>{error}</span>
              <button className="shrink-0 text-xs underline" onClick={() => setError(null)}>
                dismiss
              </button>
            </AlertDescription>
          </Alert>
        </div>
      )}

      {/* ── Main area ────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        {/* Canvas */}
        <div className="flex-1 p-3 min-h-0">
          {/* Workflow name/description */}
          <div className="flex gap-2 mb-3">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Workflow name..."
              className="max-w-[240px] h-8 text-sm" // drift-allowed: node-name input pinned width matches canvas chrome sizing
            />
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Description (optional)"
              className="flex-1 h-8 text-sm"
            />
          </div>

          <WorkflowCanvas
            nodes={nodes}
            edges={edges}
            selectedNodeId={selectedNodeId}
            nodeStatuses={nodeStatuses}
            onNodeClick={handleNodeClick}
            onNodeMove={handleNodeMove}
          />

          {/* Run result summary */}
          {runResult && (
            <div className="mt-2 p-2 rounded bg-muted/40 border text-xs text-muted-foreground">
              Run <span className="text-foreground font-mono">{runResult.id.slice(0, 8)}</span>
              {" — "}
              <Badge variant={runResult.status === "completed" ? "default" : "destructive"} className="text-label-xs">
                {runResult.status}
              </Badge>
              {runResult.error && <span className="text-red-700 dark:text-red-400 ml-2">{runResult.error}</span>}
            </div>
          )}
        </div>

        {/* ── Right sidebar: node config ─────────────────────────────── */}
        {selectedNode && (
          <div className="w-[260px] border-l bg-muted/30 p-3 flex flex-col gap-3"> {/* drift-allowed: node-config side panel pinned width matches canvas chrome sizing */}
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
                <Settings2 className="h-3.5 w-3.5 text-teal-400" />
                Node Config
              </h3>
              <button aria-label="Close node config" onClick={() => setSelectedNodeId(null)} className="text-muted-foreground hover:text-foreground">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            <div>
              <Label className="text-xs text-muted-foreground">Name</Label>
              <Input
                value={selectedNode.name}
                onChange={(e) => updateNodeName(e.target.value)}
                className="mt-1 h-8 text-sm"
              />
            </div>

            <div>
              <Label className="text-xs text-muted-foreground">Type</Label>
              <div className="mt-1">
                <Badge className="text-xs capitalize">{selectedNode.type}</Badge>
              </div>
            </div>

            <div>
              <Label className="text-xs text-muted-foreground">ID</Label>
              <p className="text-xs text-muted-foreground font-mono mt-1">{selectedNode.id}</p>
            </div>

            {/* Config fields */}
            {selectedNode.type === "condition" && (
              <div>
                <Label className="text-xs text-muted-foreground">Expression</Label>
                <Input
                  value={(selectedNode.config.expression as string) ?? ""}
                  onChange={(e) => updateNodeConfig("expression", e.target.value)}
                  placeholder="confidence > 0.5"
                  className="mt-1 h-8 text-sm font-mono"
                />
              </div>
            )}

            {/* Connected edges */}
            <div>
              <Label className="text-xs text-muted-foreground">Connections</Label>
              <div className="mt-1 space-y-1">
                {edges
                  .filter((e) => e.source_id === selectedNode.id || e.target_id === selectedNode.id)
                  .map((e, i) => (
                    <div key={i} className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>
                        {e.source_id === selectedNode.id ? `→ ${e.target_id}` : `← ${e.source_id}`}
                      </span>
                      <button
                        aria-label="Remove connection"
                        className="text-red-500/60 hover:text-red-700 dark:text-red-400"
                        onClick={() =>
                          setEdges((prev) =>
                            prev.filter((edge) => !(edge.source_id === e.source_id && edge.target_id === e.target_id)),
                          )
                        }
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                {edges.filter((e) => e.source_id === selectedNode.id || e.target_id === selectedNode.id).length === 0 && (
                  <p className="text-xs text-muted-foreground/70">No connections</p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
