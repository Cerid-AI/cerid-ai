import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { DollarSign } from "lucide-react"
import type { AuditCosts } from "@/lib/types"

interface CostBreakdownProps {
  costs: AuditCosts | undefined
  hours?: number
}

export function CostBreakdown({ costs, hours }: CostBreakdownProps) {
  if (!costs) return <EmptyState icon={DollarSign} title="No cost data" description="Costs are tracked when AI operations run" />

  const totalCost = Object.values(costs.estimated_cost_usd).reduce((a, b) => a + b, 0)
  const totalTokens = Object.values(costs.estimated_tokens).reduce((a, b) => a + b, 0)
  const windowHours = hours ?? costs.time_window_hours
  const monthlyProjection = windowHours > 0 ? (totalCost / windowHours) * 730 : 0

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Cost Breakdown</CardTitle>
          <span className="text-xs text-muted-foreground">{costs.time_window_hours}h window</span>
        </div>
      </CardHeader>
      <CardContent className="p-3">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {/* Total */}
          <div className="rounded-lg bg-muted/50 p-2.5">
            <p className="text-xs text-muted-foreground">Total Cost</p>
            <p className="text-lg font-semibold">${totalCost.toFixed(4)}</p>
            <p className="text-xs text-muted-foreground">{totalTokens.toLocaleString()} tokens</p>
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
