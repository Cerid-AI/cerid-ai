import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { BarChart3 } from "lucide-react"
import type { MaintenanceCollections } from "@/lib/types"

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
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 8 }}
              formatter={(value) => [(value ?? 0).toLocaleString(), "Chunks"]}
            />
            <Bar dataKey="chunks" fill="hsl(var(--chart-1))" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
        <p className="mt-1 text-xs text-muted-foreground">
          Total: {collections.total_chunks.toLocaleString()} chunks across {data.length} collections
        </p>
      </CardContent>
    </Card>
  )
}
