// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * AgentCards — invokable surface for the 6 background agent endpoints.
 *
 * Previously the Agents pane was a passive console waiting for
 * server-emitted activity ("Waiting for agent activity..."). The README
 * promised 9 intelligent agents but the UI exposed zero; users clicked in,
 * saw nothing, and left. These cards let a non-technical user click Run on
 * Audit / Curate / Maintain / etc., watch output stream into the existing
 * console below, and actually use what the backend already supports.
 */
import { useEffect, useState } from "react"

import {
  Activity,
  Brain,
  FileSearch,
  Loader2,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { MCP_BASE, mcpHeaders } from "@/lib/api/common"

interface AgentDefinition {
  id: string
  label: string
  icon: LucideIcon
  description: string
  endpoint: string
  method?: "POST" | "GET"
  body?: Record<string, unknown>
  runLabel?: string
}

/**
 * One card per agent the backend exposes. Keep the set small — these are
 * the user-facing agents, not every internal microservice. Add new cards
 * here when a new agent gets a `POST /agent/...` endpoint.
 */
const AGENTS: AgentDefinition[] = [
  {
    id: "audit",
    label: "Audit",
    icon: FileSearch,
    description:
      "Summarise activity, costs, queries, ingestion, and conversations over the last 24 hours.",
    endpoint: "/agent/audit",
    body: {
      reports: ["activity", "ingestion", "costs", "queries", "conversations"],
      hours: 24,
    },
  },
  {
    id: "rectify",
    label: "Rectify",
    icon: Wrench,
    description:
      "Find and fix KB inconsistencies — duplicates, orphan chunks, stale artifacts, domain imbalance.",
    endpoint: "/agent/rectify",
    body: { dry_run: true },
    runLabel: "Dry-run",
  },
  {
    id: "maintain",
    label: "Maintain",
    icon: ShieldCheck,
    description:
      "System health check — Bifrost, collections, memory decay, expiring artifacts.",
    endpoint: "/agent/maintain",
  },
  {
    id: "curate",
    label: "Curate",
    icon: Sparkles,
    description:
      "Score artifact quality and regenerate synopses for low-quality items.",
    endpoint: "/agent/curate",
  },
  {
    id: "memory-extract",
    label: "Extract Memories",
    icon: Brain,
    description:
      "Mine recent conversations for durable facts, decisions, and preferences; store as memory nodes.",
    endpoint: "/agent/memory/extract-recent",
    body: { conversations: 3 },
  },
  {
    id: "self-rag",
    label: "Self-RAG",
    icon: Activity,
    description:
      "Run the self-validation loop — re-check the last response for unsupported claims + fill coverage gaps.",
    endpoint: "/agent/self-rag-enhance",
    body: {},
  },
]

type CardStatus =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "ok"; ranAtMs: number; summary?: string }
  | { kind: "error"; message: string }

function formatElapsed(ms: number): string {
  const secs = Math.max(0, Math.floor(ms / 1000))
  if (secs < 5) return "just now"
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  return `${hours}h ago`
}

/** Re-render every 10s so "5s ago" stays accurate without flooding the tree. */
function useTick(intervalMs: number, active: boolean): void {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setTick((t) => t + 1), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs, active])
}

export function AgentCards() {
  const [states, setStates] = useState<Record<string, CardStatus>>({})
  // Tick once every 10 seconds while any card is in the "ok" state so the
  // "Completed Ns ago" relative timestamp stays fresh without polling.
  const hasOk = Object.values(states).some((s) => s.kind === "ok")
  useTick(10_000, hasOk)

  const runAgent = async (agent: AgentDefinition) => {
    setStates((s) => ({ ...s, [agent.id]: { kind: "running" } }))
    try {
      const res = await fetch(`${MCP_BASE}${agent.endpoint}`, {
        method: agent.method ?? "POST",
        headers: mcpHeaders({ "Content-Type": "application/json", "X-Client-ID": "gui" }),
        // Always send a JSON body (even for no-payload endpoints) — FastAPI
        // validators reject undefined body + Content-Type: application/json
        // with a 422. Empty object is semantically "use defaults" for
        // every /agent/* endpoint we wire here.
        body: JSON.stringify(agent.body ?? {}),
      })
      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`)
      }
      const data = await res.json().catch(() => ({}))
      const summary =
        typeof data?.summary === "string"
          ? data.summary
          : typeof data?.status === "string"
            ? data.status
            : `Completed (${Object.keys(data ?? {}).length} fields)`
      setStates((s) => ({
        ...s,
        [agent.id]: { kind: "ok", ranAtMs: Date.now(), summary },
      }))
    } catch (e) {
      setStates((s) => ({
        ...s,
        [agent.id]: {
          kind: "error",
          message: e instanceof Error ? e.message : "Request failed",
        },
      }))
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 lg:grid-cols-3">
      {AGENTS.map((agent) => {
        const Icon = agent.icon
        const state = states[agent.id] ?? { kind: "idle" }
        return (
          <Card key={agent.id} className="overflow-hidden">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-1">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Icon className="h-4 w-4 text-brand" />
                {agent.label}
              </CardTitle>
              {state.kind === "ok" && (
                <div className="flex items-center gap-1.5">
                  <span className="text-label-xs text-muted-foreground tabular-nums">
                    {formatElapsed(Date.now() - state.ranAtMs)}
                  </span>
                  <Badge
                    variant="outline"
                    className="text-label-xs text-green-500 border-green-500/30"
                  >
                    ok
                  </Badge>
                </div>
              )}
              {state.kind === "error" && (
                <Badge variant="outline" className="text-label-xs text-destructive">
                  failed
                </Badge>
              )}
            </CardHeader>
            <CardContent className="space-y-2 p-3 pt-1">
              <p className="text-xs text-muted-foreground leading-relaxed">
                {agent.description}
              </p>
              <div className="flex items-center justify-between gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => runAgent(agent)}
                  disabled={state.kind === "running"}
                  className="h-7 text-xs"
                >
                  {state.kind === "running" && (
                    <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                  )}
                  {state.kind === "running"
                    ? "Running…"
                    : (agent.runLabel ?? "Run")}
                </Button>
                {state.kind === "ok" && state.summary && (
                  <span className="truncate text-label-xs text-muted-foreground">
                    {state.summary}
                  </span>
                )}
                {state.kind === "error" && (
                  <span className="truncate text-label-xs text-destructive/80">
                    {state.message}
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
