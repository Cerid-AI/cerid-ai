// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { MessageSquare } from "lucide-react"
import type { AuditConversations } from "@/lib/types"

interface ConversationStatsProps {
  conversations: AuditConversations | undefined
}

export function ConversationStats({ conversations }: ConversationStatsProps) {
  if (!conversations) return <EmptyState icon={MessageSquare} title="No conversation data" description="Stats appear after chat usage" />

  const modelData = Object.entries(conversations.models)
    .sort((a, b) => b[1].turns - a[1].turns)

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Conversations</CardTitle>
          <span className="text-xs text-muted-foreground">
            {conversations.total_conversations} conversations, {conversations.total_turns} turns
          </span>
        </div>
      </CardHeader>
      <CardContent className="p-3">
        {conversations.total_cost_usd > 0 && (
          <p className="mb-3 text-xs text-muted-foreground">
            Total estimated cost: <span className="font-medium text-foreground">${conversations.total_cost_usd.toFixed(4)}</span>
          </p>
        )}
        {modelData.length > 0 ? (
          <div className="space-y-2">
            {modelData.map(([model, stats]) => (
              <div key={model} className="rounded-lg border p-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium">{model}</span>
                  <span className="text-xs text-muted-foreground">{stats.turns} turns</span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
                  <span>{stats.input_tokens.toLocaleString()} in</span>
                  <span>{stats.output_tokens.toLocaleString()} out</span>
                  {stats.avg_latency_ms > 0 && <span>{Math.round(stats.avg_latency_ms)}ms avg</span>}
                  {stats.cost_usd > 0 && <span>${stats.cost_usd.toFixed(4)}</span>}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-4 text-center text-xs text-muted-foreground">No model usage data</p>
        )}
      </CardContent>
    </Card>
  )
}