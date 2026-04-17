// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

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
import { useState } from "react"

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
    endpoint: "/agent/extract-memories",
  },
  {
    id: "self-rag",
    label: "Self-RAG",
    icon: Activity,
    description:
      "Run the self-validation loop — re-check the last response for unsupported claims + fill coverage gaps.",
    endpoint: "/agent/self-rag-enhance",
  },
]

type CardStatus =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "ok"; ranAtMs: number; summary?: string }
  | { kind: "error"; message: string }

export function AgentCards() {
  const [states, setStates] = useState<Record<string, CardStatus>>({})

  const runAgent = async (agent: AgentDefinition) => {
    setStates((s) => ({ ...s, [agent.id]: { kind: "running" } }))
    try {
      const res = await fetch(`${MCP_BASE}${agent.endpoint}`, {
        method: agent.method ?? "POST",
        headers: mcpHeaders({ "Content-Type": "application/json", "X-Client-ID": "gui" }),
        body: agent.body ? JSON.stringify(agent.body) : undefined,
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
                <Badge
                  variant="outline"
                  className="text-[10px] text-green-500 border-green-500/30"
                >
                  ok
                </Badge>
              )}
              {state.kind === "error" && (
                <Badge variant="outline" className="text-[10px] text-destructive">
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
                  <span className="truncate text-[10px] text-muted-foreground">
                    {state.summary}
                  </span>
                )}
                {state.kind === "error" && (
                  <span className="truncate text-[10px] text-destructive/80">
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
