// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// LLM cost Sankey — Phase L Day 3 + Tier A T4b four-state.

import { Sankey, Tooltip, ResponsiveContainer, Rectangle, Layer } from "recharts"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { DollarSign, Loader2, Sparkles } from "lucide-react"
import { useCostByStage } from "@/hooks/use-analytics"

interface CostSankeyProps {
  windowDays?: number
  tier?: string
}

export function CostSankey({ windowDays = 30, tier = "community" }: CostSankeyProps) {
  const isPro = tier !== "community"
  const { data, isLoading, isError, error, refetch } = useCostByStage(windowDays, isPro)

  if (!isPro) {
    return (
      <Card className="p-4 relative" data-testid="cost-sankey">
        <div className="opacity-30 pointer-events-none">
          <CostSankeyHeader windowDays={windowDays} totalCostUsd={0} />
          <div className="h-72 flex items-center justify-center text-muted-foreground">
            Sankey preview
          </div>
        </div>
        <div className="absolute inset-0 flex items-center justify-center backdrop-blur-sm">
          <div className="text-center p-4">
            <Sparkles className="w-8 h-8 mx-auto text-amber-500 mb-2" aria-hidden="true" />
            <h4 className="text-sm font-semibold">Cost Sankey is Pro-tier</h4>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              See where every dollar of LLM budget goes — by pipeline phase + stage.
            </p>
          </div>
        </div>
      </Card>
    )
  }

  if (isLoading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground h-80" role="status">
        <Loader2 className="w-4 h-4 animate-spin mr-2" aria-hidden="true" />
        Loading cost telemetry…
      </Card>
    )
  }

  if (isError) {
    return (
      <Card className="p-4 border-amber-500/30 bg-amber-500/5" data-testid="cost-sankey">
        <div className="text-sm text-amber-700 dark:text-amber-400" role="alert">
          {error instanceof Error ? error.message : "Failed to load"}
        </div>
        <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => void refetch()}>
          Retry
        </Button>
      </Card>
    )
  }

  if (!data || data.edges.length === 0) {
    return (
      <div data-testid="cost-sankey">
        <EmptyState
          icon={DollarSign}
          title={`No LLM cost recorded in the last ${windowDays} days`}
          description="Costs appear here once chat, ingest enrichment, or verification use a metered provider."
        />
      </div>
    )
  }

  const providers = Array.from(new Set(data.edges.map((e) => e.source)))
  const stages = Array.from(new Set(data.edges.map((e) => e.target)))
  const nodeList = [
    ...providers.map((name) => ({ name, key: `src:${name}` })),
    ...stages.map((name) => ({ name, key: `dst:${name}` })),
  ]
  const nodeIndex = new Map(nodeList.map((n, i) => [n.key, i]))
  const links = data.edges.map((e) => ({
    source: nodeIndex.get(`src:${e.source}`) ?? 0,
    target: nodeIndex.get(`dst:${e.target}`) ?? 0,
    value: e.value,
  }))

  return (
    <Card className="p-4" data-testid="cost-sankey">
      <CostSankeyHeader windowDays={windowDays} totalCostUsd={data.total_cost_usd} />
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <Sankey
            data={{ nodes: nodeList, links }}
            nodeWidth={10}
            nodePadding={20}
            linkCurvature={0.5}
            iterations={32}
            node={<SankeyNode />}
            link={{ stroke: "var(--chart-2)", strokeOpacity: 0.4 }}
          >
            <Tooltip
              formatter={((value: number) => [`$${value.toFixed(4)}`, "cost"]) as never}
            />
          </Sankey>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}

function CostSankeyHeader({ windowDays, totalCostUsd }: {
  windowDays: number
  totalCostUsd: number
}) {
  return (
    <div className="flex items-start justify-between mb-3">
      <div>
        <h3 className="text-base font-semibold flex items-center gap-2">
          <DollarSign className="w-4 h-4" aria-hidden="true" />
          LLM Cost Flow
        </h3>
        <p className="text-xs text-muted-foreground">
          Last {windowDays} days · ${totalCostUsd.toFixed(4)} total
        </p>
      </div>
    </div>
  )
}

interface SankeyNodeProps {
  x?: number
  y?: number
  width?: number
  height?: number
  payload?: { name?: string; sourceLinks?: unknown[] }
}

function SankeyNode(props: SankeyNodeProps) {
  const { x = 0, y = 0, width = 10, height = 0, payload } = props
  const isLeft = (payload?.sourceLinks?.length ?? 0) > 0
  return (
    <Layer>
      <Rectangle
        x={x}
        y={y}
        width={width}
        height={height}
        fill="var(--chart-1)"
        fillOpacity={0.9}
      />
      <text
        x={isLeft ? x - 4 : x + width + 4}
        y={y + height / 2}
        textAnchor={isLeft ? "end" : "start"}
        dominantBaseline="middle"
        fontSize={11}
        fill="var(--foreground)"
      >
        {payload?.name}
      </text>
    </Layer>
  )
}
