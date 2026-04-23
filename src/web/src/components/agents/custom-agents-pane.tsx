// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CustomAgentsPane — Sprint 1 GUI for the user-defined agents surface.
 *
 * Backend ships the full CRUD at `/custom-agents/*` (Stage A: templated
 * agents with system prompt + tool allowlist + KB domain scope + model
 * override). This pane exposes the read + create-from-template flow.
 * Edit/delete buttons hit the same endpoints. When STRICT_AGENTS_ONLY=true
 * the backend returns 403 on every endpoint — this pane surfaces a
 * warning banner instead of letting the user click into errors.
 */
import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Bot, Plus, Trash2, ShieldX, Loader2, AlertTriangle, X } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { fetchSettings } from "@/lib/api/settings"
import {
  listCustomAgents,
  listAgentTemplates,
  createAgentFromTemplate,
  deleteCustomAgent,
  type CustomAgentDefinition,
  type AgentTemplate,
} from "@/lib/api/custom-agents"
import { logSwallowedError } from "@/lib/log-swallowed"

export default function CustomAgentsPane() {
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: fetchSettings })
  const strict = settings?.strict_agents_only ?? false

  const [agents, setAgents] = useState<CustomAgentDefinition[]>([])
  const [templates, setTemplates] = useState<AgentTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showTemplatePicker, setShowTemplatePicker] = useState(false)
  const [actionPending, setActionPending] = useState<string | null>(null)

  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      const [agentsRes, templatesRes] = await Promise.all([
        listCustomAgents(),
        listAgentTemplates(),
      ])
      setAgents(agentsRes.agents)
      setTemplates(templatesRes.templates)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "load failed"
      setError(msg)
      logSwallowedError(err, "custom-agents.reload")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!strict) {
      reload().catch((err) => logSwallowedError(err, "custom-agents.initial-load"))
    } else {
      setLoading(false)
    }
  }, [strict])

  const handleCreate = async (templateId: string) => {
    setActionPending(`create:${templateId}`)
    try {
      await createAgentFromTemplate(templateId)
      setShowTemplatePicker(false)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : "create failed")
    } finally {
      setActionPending(null)
    }
  }

  const handleDelete = async (agentId: string) => {
    if (!confirm("Delete this custom agent? This cannot be undone.")) return
    setActionPending(`delete:${agentId}`)
    try {
      await deleteCustomAgent(agentId)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : "delete failed")
    } finally {
      setActionPending(null)
    }
  }

  if (strict) {
    return (
      <div className="flex h-full flex-col">
        <div className="border-b px-4 py-3">
          <h2 className="text-sm font-medium">Custom Agents</h2>
        </div>
        <div className="p-4">
          <Card className="border-red-500/30 bg-red-500/5">
            <CardContent className="flex items-start gap-3 pt-4">
              <ShieldX className="h-5 w-5 shrink-0 text-red-600 dark:text-red-400" />
              <div className="grid gap-1">
                <p className="text-sm font-medium text-red-600 dark:text-red-400">
                  Custom agents are disabled in this deployment
                </p>
                <p className="text-xs text-muted-foreground">
                  <code className="font-mono">STRICT_AGENTS_ONLY=true</code> in <code className="font-mono">.env</code>.
                  All <code className="font-mono">/custom-agents</code> endpoints return 403.
                  Built-in 10 specialist agents remain available on the Agents page.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <h2 className="text-sm font-medium">Custom Agents</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            User-defined agents with custom system prompt, tool allowlist, KB domain scope, and model override.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => setShowTemplatePicker(true)}
          disabled={loading || templates.length === 0}
        >
          <Plus className="mr-1 h-3 w-3" /> New Agent
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {error && (
          <Card className="mb-3 border-yellow-500/30 bg-yellow-500/5">
            <CardContent className="flex items-start gap-2 pt-4">
              <AlertTriangle className="h-4 w-4 shrink-0 text-yellow-600 dark:text-yellow-400" />
              <p className="text-xs text-yellow-700 dark:text-yellow-300">{error}</p>
            </CardContent>
          </Card>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading custom agents…
          </div>
        ) : agents.length === 0 ? (
          <EmptyState
            templateCount={templates.length}
            onCreate={() => setShowTemplatePicker(true)}
          />
        ) : (
          <div className="grid gap-2">
            {agents.map((agent) => (
              <AgentRow
                key={agent.agent_id}
                agent={agent}
                pending={actionPending === `delete:${agent.agent_id}`}
                onDelete={() => agent.agent_id && handleDelete(agent.agent_id)}
              />
            ))}
          </div>
        )}
      </div>

      {showTemplatePicker && (
        <TemplatePicker
          templates={templates}
          onPick={handleCreate}
          onClose={() => setShowTemplatePicker(false)}
          actionPending={actionPending}
        />
      )}
    </div>
  )
}

function EmptyState({ templateCount, onCreate }: { templateCount: number; onCreate: () => void }) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
        <Bot className="h-8 w-8 text-muted-foreground" />
        <div>
          <p className="text-sm font-medium">No custom agents yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Start from one of {templateCount} built-in templates
            (research-assistant, code-reviewer, fact-checker, knowledge-curator).
          </p>
        </div>
        <Button size="sm" onClick={onCreate} disabled={templateCount === 0}>
          <Plus className="mr-1 h-3 w-3" /> Create from Template
        </Button>
      </CardContent>
    </Card>
  )
}

function AgentRow({
  agent,
  pending,
  onDelete,
}: {
  agent: CustomAgentDefinition
  pending: boolean
  onDelete: () => void
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2 p-3">
        <div className="min-w-0">
          <CardTitle className="text-sm">{agent.name}</CardTitle>
          {agent.description && (
            <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{agent.description}</p>
          )}
          <div className="mt-2 flex flex-wrap gap-1">
            {agent.template_id && (
              <Badge variant="outline" className="text-[10px]">tmpl: {agent.template_id}</Badge>
            )}
            {agent.rag_mode && (
              <Badge variant="outline" className="text-[10px]">rag: {agent.rag_mode}</Badge>
            )}
            {agent.model_override && (
              <Badge variant="outline" className="text-[10px] font-mono">{agent.model_override}</Badge>
            )}
            {agent.domains && agent.domains.length > 0 && (
              <Badge variant="outline" className="text-[10px]">
                domains: {agent.domains.join(",")}
              </Badge>
            )}
            {agent.tools && agent.tools.length > 0 && (
              <Badge variant="outline" className="text-[10px]">tools: {agent.tools.length}</Badge>
            )}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDelete}
          disabled={pending}
          aria-label="Delete agent"
          className="h-7 w-7 text-muted-foreground hover:text-red-600"
        >
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
        </Button>
      </CardHeader>
    </Card>
  )
}

function TemplatePicker({
  templates,
  onPick,
  onClose,
  actionPending,
}: {
  templates: AgentTemplate[]
  onPick: (id: string) => void
  onClose: () => void
  actionPending: string | null
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="max-h-[80vh] w-[90vw] max-w-lg overflow-auto rounded-lg border bg-card p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-medium">Create Custom Agent from Template</h3>
          <Button variant="ghost" size="icon" onClick={onClose} className="h-6 w-6">
            <X className="h-3 w-3" />
          </Button>
        </div>
        <div className="grid gap-2">
          {templates.map((tpl) => {
            const pending = actionPending === `create:${tpl.template_id}`
            return (
              <Card
                key={tpl.template_id}
                className={`cursor-pointer transition-colors ${pending ? "opacity-60" : "hover:border-primary/50"}`}
                onClick={() => !pending && onPick(tpl.template_id)}
              >
                <CardContent className="flex items-start gap-2 pt-3 pb-3">
                  <Bot className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{tpl.name}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{tpl.description}</p>
                  </div>
                  {pending && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />}
                </CardContent>
              </Card>
            )
          })}
        </div>
      </div>
    </div>
  )
}
