// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AgentsPane — stacks the invokable <AgentCards> grid on top of the
 * streaming <AgentConsole>. This turns the previously-empty Agents tab
 * into an actionable surface: click Run on any card and watch the
 * backend agent's activity stream into the console below.
 */
import AgentConsole from "@/components/agents/agent-console"
import { AgentCards } from "@/components/agents/agent-cards"

export default function AgentsPane() {
  return (
    <div className="flex h-full flex-col">
      <div className="border-b px-4 py-3">
        <h2 className="text-sm font-medium">Agents</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Invoke any background agent. Activity streams into the console below.
        </p>
      </div>
      <div className="shrink-0 border-b">
        <AgentCards />
      </div>
      <div className="min-h-0 flex-1">
        <AgentConsole />
      </div>
    </div>
  )
}
