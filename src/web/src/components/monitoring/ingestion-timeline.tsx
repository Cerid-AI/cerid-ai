import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { EmptyState } from "@/components/ui/empty-state"
import { Inbox } from "lucide-react"
import type { IngestLogEntry } from "@/lib/types"

const EVENT_STYLES: Record<string, string> = {
  ingest: "bg-green-500/10 text-green-700 dark:text-green-400",
  duplicate: "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400",
  recategorize: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
  query: "bg-zinc-500/10 text-zinc-700 dark:text-zinc-400",
  error: "bg-red-500/10 text-red-700 dark:text-red-400",
  scheduled_job: "bg-purple-500/10 text-purple-700 dark:text-purple-400",
}

function formatRelativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

interface IngestionTimelineProps {
  entries: IngestLogEntry[] | undefined
}

export function IngestionTimeline({ entries }: IngestionTimelineProps) {
  if (!entries || entries.length === 0) {
    return <EmptyState icon={Inbox} title="No recent activity" description="Ingest files to see activity here" />
  }

  const recent = entries.slice(0, 20)

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <CardTitle className="text-sm">Recent Activity</CardTitle>
      </CardHeader>
      <CardContent className="p-3">
        <div className="space-y-1">
          {recent.map((entry, i) => (
            <div
              key={`${entry.timestamp}-${i}`}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs odd:bg-muted/30"
            >
              <Badge
                variant="outline"
                className={`text-[10px] ${EVENT_STYLES[entry.event] ?? ""}`}
              >
                {entry.event}
              </Badge>
              <span className="min-w-0 flex-1 truncate">{entry.filename || entry.artifact_id?.slice(0, 8)}</span>
              {entry.domain && (
                <span className="text-muted-foreground capitalize">{entry.domain}</span>
              )}
              <span className="shrink-0 text-muted-foreground">{formatRelativeTime(entry.timestamp)}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
