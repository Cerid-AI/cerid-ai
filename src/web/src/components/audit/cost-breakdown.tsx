// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { DollarSign } from "lucide-react"
import type { AuditCosts } from "@/lib/types"

interface CostBreakdownProps {
  costs: AuditCosts | undefined
  /**
   * The period the pane's selector is showing. Used only to say that this
   * report is NOT scoped to it: core/agents/audit.py calls estimate_costs()
   * without an hours argument, so the figures always cover that function's own
   * 720h default whatever the user picked.
   */
  selectedRangeLabel?: string
}

export function CostBreakdown({ costs, selectedRangeLabel }: CostBreakdownProps) {
  if (!costs) return <EmptyState icon={DollarSign} title="No cost data" description="Costs are tracked when AI operations run" />

  const totalCost = Object.values(costs.estimated_cost_usd).reduce((a, b) => a + b, 0)
  const totalTokens = Object.values(costs.estimated_tokens).reduce((a, b) => a + b, 0)
  // Project from the window the figures actually cover. Projecting from the
  // selected period instead divided a 30-day cost by 24 hours and inflated the
  // monthly number thirtyfold at the pane's default selection.
  const windowHours = costs.time_window_hours
  const monthlyProjection = windowHours > 0 ? (totalCost / windowHours) * 730 : 0

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Cost Breakdown</CardTitle>
          <span className="text-xs text-muted-foreground">
            {costs.time_window_hours}h window
            {selectedRangeLabel ? ` · not the ${selectedRangeLabel} selection` : ""}
          </span>
        </div>
      </CardHeader>
      <CardContent className="p-3">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {/* Total */}
          <div className="rounded-lg bg-muted/50 p-2.5">
            <p className="text-xs text-muted-foreground">Estimated Cost</p>
            <p className="text-lg font-semibold">${totalCost.toFixed(4)}</p>
            <p className="text-xs text-muted-foreground">{totalTokens.toLocaleString()} tokens</p>
            <p className="text-xs text-muted-foreground">from operation counts</p>
          </div>

          {/* Monthly projection */}
          <div className="rounded-lg bg-muted/50 p-2.5">
            <p className="text-xs text-muted-foreground">Monthly Projection</p>
            <p className="text-lg font-semibold">${monthlyProjection.toFixed(2)}</p>
            <p className="text-xs text-muted-foreground">based on {windowHours}h window</p>
          </div>

          {/* Per-tier breakdown */}
          {Object.entries(costs.estimated_cost_usd)
            .filter(([tier]) => tier !== "total")
            .map(([tier, cost]) => {
              const opKey = tier === "rerank" ? "rerank" : `categorize_${tier}`
              return (
                <div key={tier} className="rounded-lg bg-muted/50 p-2.5">
                  <p className="text-xs capitalize text-muted-foreground">{tier.replace(/_/g, " ")}</p>
                  <p className="text-sm font-medium">${cost.toFixed(4)}</p>
                  <p className="text-xs text-muted-foreground">
                    {(costs.estimated_tokens[tier] ?? 0).toLocaleString()} tokens
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {(costs.operations[opKey] ?? 0).toLocaleString()} operations
                  </p>
                </div>
              )
            })}
        </div>
      </CardContent>
    </Card>
  )
}