// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { BookOpen, Loader2, Inbox } from "lucide-react"
import type { DigestResponse } from "@/lib/types"

interface DigestCardProps {
  digest: DigestResponse | undefined
  isLoading: boolean
  onPeriodChange?: (hours: number) => void
}

const PERIOD_OPTIONS = [
  { value: "24", label: "Last 24 hours" },
  { value: "72", label: "Last 3 days" },
  { value: "168", label: "Last 7 days" },
]

function formatRelativeTime(isoDate: string): string {
  const now = Date.now()
  const then = new Date(isoDate).getTime()
  const diffMs = now - then
  const diffMin = Math.floor(diffMs / 60_000)
  if (diffMin < 1) return "just now"
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHours = Math.floor(diffMin / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays}d ago`
}

function periodLabel(hours: number): string {
  if (hours <= 24) return "24 hours"
  if (hours <= 72) return "3 days"
  return "7 days"
}

export function DigestCard({ digest, isLoading, onPeriodChange }: DigestCardProps) {
  const periodHours = digest?.period_hours ?? 24

  const handlePeriodChange = (value: string) => {
    onPeriodChange?.(Number(value))
  }

  const isEmpty =
    !digest ||
    (digest.artifacts.count === 0 &&
      digest.relationships.new_count === 0 &&
      digest.recent_events === 0)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <BookOpen className="h-4 w-4" />
          Knowledge Digest
        </CardTitle>
        <Select
          defaultValue="24"
          onValueChange={handlePeriodChange}
        >
          <SelectTrigger className="h-7 w-[140px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PERIOD_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {digest?.generated_at && (
          <span className="text-[10px] tabular-nums text-muted-foreground">
            {formatRelativeTime(digest.generated_at)}
          </span>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading digest…
          </div>
        ) : isEmpty ? (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
            <Inbox className="mb-2 h-8 w-8" />
            <p className="text-sm">No activity in the last {periodLabel(periodHours)}.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Summary stats */}
            <div className="grid grid-cols-4 gap-2 text-center">
              <div>
                <div className="text-2xl font-bold">{digest.artifacts.count}</div>
                <div className="text-xs text-muted-foreground">Artifacts</div>
              </div>
              <div>
                <div className="text-2xl font-bold">
                  {Object.keys(digest.artifacts.by_domain).length}
                </div>
                <div className="text-xs text-muted-foreground">Domains</div>
              </div>
              <div>
                <div className="text-2xl font-bold">{digest.relationships.new_count}</div>
                <div className="text-xs text-muted-foreground">Relationships</div>
              </div>
              <div>
                <div className="text-2xl font-bold">{digest.recent_events}</div>
                <div className="text-xs text-muted-foreground">Events</div>
              </div>
            </div>

            {/* Domain breakdown */}
            {Object.keys(digest.artifacts.by_domain).length > 0 && (
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  By Domain
                </div>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(digest.artifacts.by_domain).map(([domain, count]) => (
                    <Badge key={domain} variant="secondary" className="text-xs">
                      {count} {domain}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* Recent artifacts */}
            {digest.artifacts.items.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  Recent Artifacts
                </div>
                <ul className="space-y-1">
                  {digest.artifacts.items.slice(0, 10).map((item) => (
                    <li
                      key={item.id}
                      className="flex items-center justify-between text-xs"
                    >
                      <span className="flex items-center gap-1.5 truncate">
                        <span className="truncate font-medium">{item.filename}</span>
                        <Badge variant="outline" className="shrink-0 text-[10px]">
                          {item.domain}
                        </Badge>
                      </span>
                      <span className="shrink-0 text-muted-foreground">
                        {formatRelativeTime(item.ingested_at)}
                      </span>
                    </li>
                  ))}
                </ul>
                {digest.artifacts.items.length > 10 && (
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {digest.artifacts.items.length - 10} more…
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
