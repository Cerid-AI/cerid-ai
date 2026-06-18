// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// LLM cost Sankey — Phase L Day 3.
//
// Pro-only. Visualizes per-stage LLM spend over the last 30 days. Left
// column = pipeline phase (ingest / retrieval / verification / curator
// / pro_features / other). Right column = individual stage. Flow width =
// dollars spent.

import { useEffect, useState } from "react"
import { Sankey, Tooltip, ResponsiveContainer, Rectangle, Layer } from "recharts"
import { Card } from "@/components/ui/card"
import { DollarSign, Loader2, Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"
import { fetchCostByStage, type CostByStageResponse } from "@/lib/api/analytics"

interface CostSankeyProps {
  windowDays?: number
  tier?: string
}

export function CostSankey({ windowDays = 30, tier = "community" }: CostSankeyProps) {
  const [data, setData] = useState<CostByStageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const isPro = tier !== "community"

  useEffect(() => {
    if (!isPro) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    fetchCostByStage(windowDays)
      .then((d) => {
        if (!cancelled) {
          setData(d)
          setError(null)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [windowDays, isPro])

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
            <Sparkles className="w-8 h-8 mx-auto text-amber-500 mb-2" />
            <h4 className="text-sm font-semibold">Cost Sankey is Pro-tier</h4>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              See where every dollar of LLM budget goes — by pipeline phase + stage.
            </p>
          </div>
        </div>
      </Card>
    )
  }

  if (loading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground h-80">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading cost telemetry…
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="p-4 border-amber-500/30 bg-amber-500/5">
        <div className="text-sm text-amber-600" role="alert">{error}</div>
      </Card>
    )
  }

  if (!data || data.edges.length === 0) {
    return (
      <Card className="p-4" data-testid="cost-sankey">
        <CostSankeyHeader windowDays={windowDays} totalCostUsd={0} />
        <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
          No LLM cost recorded in the last {windowDays} days.
        </div>
      </Card>
    )
  }

  // Build recharts Sankey nodes + links from the edge list. Nodes need
  // to be unique by name; we collect them in order so the column
  // visualization is stable (providers on the left, stages on the right).
  const providers = Array.from(new Set(data.edges.map((e) => e.source)))
  const stages = Array.from(new Set(data.edges.map((e) => e.target)))
  const nodeList = [...providers, ...stages].map((name) => ({ name }))
  const nodeIndex = new Map(nodeList.map((n, i) => [n.name, i]))
  const links = data.edges.map((e) => ({
    source: nodeIndex.get(e.source) ?? 0,
    target: nodeIndex.get(e.target) ?? 0,
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
            link={{ stroke: "#0891b2", strokeOpacity: 0.4 }}
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
          <DollarSign className="w-4 h-4" />
          LLM Cost Flow
        </h3>
        <p className="text-xs text-muted-foreground">
          Last {windowDays} days · ${totalCostUsd.toFixed(4)} total
        </p>
      </div>
    </div>
  )
}

// Custom node renderer — labels overflow the recharts default
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
        fill="#06b6d4"
        fillOpacity={0.9}
      />
      <text
        x={isLeft ? x - 4 : x + width + 4}
        y={y + height / 2}
        textAnchor={isLeft ? "end" : "start"}
        dominantBaseline="middle"
        className={cn("text-[10px] fill-foreground")}
      >
        {payload?.name}
      </text>
    </Layer>
  )
}
