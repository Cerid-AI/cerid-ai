// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { BarChart3 } from "lucide-react"
import type { MaintenanceCollections } from "@/lib/types"

const DOMAIN_COLORS: Record<string, string> = {
  coding: "hsl(var(--chart-1))",
  finance: "hsl(var(--chart-2))",
  research: "hsl(var(--chart-3))",
  personal: "hsl(var(--chart-4))",
  general: "hsl(var(--chart-5))",
  conversations: "hsl(215 20% 65%)",
}

const FALLBACK_COLOR = "hsl(var(--chart-1))"

function getDomainColor(domain: string): string {
  return DOMAIN_COLORS[domain] ?? FALLBACK_COLOR
}

interface CustomTooltipProps {
  active?: boolean
  payload?: { value: number; payload: { name: string; chunks: number } }[]
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null
  const entry = payload[0]
  const domain = entry.payload.name
  return (
    <div
      style={{
        fontSize: 12,
        borderRadius: 8,
        backgroundColor: "hsl(var(--popover))",
        color: "hsl(var(--popover-foreground))",
        border: "1px solid hsl(var(--border))",
        padding: "8px 12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span
          style={{
            display: "inline-block",
            width: 10,
            height: 10,
            borderRadius: 2,
            backgroundColor: getDomainColor(domain),
            flexShrink: 0,
          }}
        />
        <span>{domain}: {(entry.value ?? 0).toLocaleString()} Chunks</span>
      </div>
    </div>
  )
}

interface CollectionChartProps {
  collections: MaintenanceCollections | undefined
}

export function CollectionChart({ collections }: CollectionChartProps) {
  if (!collections) {
    return <EmptyState icon={BarChart3} title="No collections" description="Collections appear after ingesting files" />
  }

  const entries = collections?.collections
  if (!entries || typeof entries !== "object") {
    return <EmptyState icon={BarChart3} title="No collections" description="Collections appear after ingesting files" />
  }

  const data = Object.entries(entries).map(([name, info]) => ({
    name: name.replace("domain_", ""),
    chunks: info.chunks,
  }))

  if (data.length === 0) {
    return <EmptyState icon={BarChart3} title="No collections" description="Collections appear after ingesting files" />
  }

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <CardTitle className="text-sm">Collection Sizes</CardTitle>
      </CardHeader>
      <CardContent className="p-3">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={data}>
            <XAxis dataKey="name" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: "hsl(var(--muted))" }} />
            <Bar dataKey="chunks" radius={[4, 4, 0, 0]}>
              {data.map((entry) => (
                <Cell key={entry.name} fill={getDomainColor(entry.name)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <p className="mt-1 text-xs text-muted-foreground">
          Total: {collections.total_chunks.toLocaleString()} chunks across {data.length} collections
        </p>
      </CardContent>
    </Card>
  )
}
