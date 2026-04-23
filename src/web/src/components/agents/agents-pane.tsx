// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AgentsPane — stacks the invokable <AgentCards> grid (built-in agents)
 * + <CustomAgentsPane> (user-defined Stage A agents) on top of the
 * streaming <AgentConsole>.
 *
 * Sub-tabs let the user switch between built-in agents (the original
 * surface) and the new custom-agents builder added Sprint 1C. Both share
 * the same activity console below — Audit/Rectify/Maintain output and
 * custom-agent invocations stream into the same place.
 */
import { useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import AgentConsole from "@/components/agents/agent-console"
import { AgentCards } from "@/components/agents/agent-cards"
import CustomAgentsPane from "@/components/agents/custom-agents-pane"

export default function AgentsPane() {
  const [tab, setTab] = useState<string>("built-in")
  return (
    <div className="flex h-full flex-col">
      <div className="border-b px-4 py-3">
        <h2 className="text-sm font-medium">Agents</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Invoke any background agent. Activity streams into the console below.
        </p>
      </div>
      <Tabs value={tab} onValueChange={setTab} className="shrink-0 border-b">
        <TabsList className="m-2">
          <TabsTrigger value="built-in">Built-in</TabsTrigger>
          <TabsTrigger value="custom">Custom Agents</TabsTrigger>
        </TabsList>
        <TabsContent value="built-in" className="m-0">
          <AgentCards />
        </TabsContent>
        <TabsContent value="custom" className="m-0">
          <CustomAgentsPane />
        </TabsContent>
      </Tabs>
      <div className="min-h-0 flex-1">
        <AgentConsole />
      </div>
    </div>
  )
}
