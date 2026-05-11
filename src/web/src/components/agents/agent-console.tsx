// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef } from "react"
import { Activity, type LucideIcon } from "lucide-react"
import {
  useAgentActivityStream,
  type ActivityEntry,
  type AgentActivityStatus,
} from "@/hooks/use-agent-activity-stream"
import { cn } from "@/lib/utils"

const AGENT_COLORS: Record<string, string> = {
  QueryAgent: "text-blue-400",
  Decomposer: "text-violet-400",
  Assembler: "text-emerald-400",
  Verifier: "text-amber-400",
  Memory: "text-pink-400",
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString("en-US", { hour12: false })
}

interface StatusDescriptor {
  dot: string
  caption: string
  pulse: boolean
}

function describeStatus(
  status: AgentActivityStatus,
  retryCount: number,
  maxRetries: number,
): StatusDescriptor {
  switch (status) {
    case "open":
      return { dot: "bg-emerald-500", caption: "Live", pulse: false }
    case "connecting":
      return { dot: "bg-amber-500", caption: "Connecting…", pulse: true }
    case "retrying":
      return {
        dot: "bg-amber-500",
        caption: `Reconnecting (attempt ${retryCount}/${maxRetries})…`,
        pulse: true,
      }
    case "unavailable":
      return { dot: "bg-red-500", caption: "Unavailable", pulse: false }
    case "idle":
    default:
      return { dot: "bg-muted-foreground/40", caption: "Idle", pulse: false }
  }
}

interface AgentConsoleProps {
  /**
   * When false, the SSE connection is torn down. The parent (Agents tab
   * host) flips this on unmount so the stream doesn't leak across tab
   * changes — fixes the "keeps polling after user navigates away" bug.
   */
  enabled?: boolean
}

const MAX_RETRIES = 10

export default function AgentConsole({ enabled = true }: AgentConsoleProps) {
  const { entries, status, error, retryCount, reset } = useAgentActivityStream({ enabled })
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries])

  const stat = describeStatus(status, retryCount, MAX_RETRIES)
  const banner = status === "unavailable" ? "unavailable" : error ? "error" : null

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <div className="relative flex h-2 w-2 items-center justify-center" aria-hidden="true">
          {stat.pulse && (
            <span className={cn("absolute h-2 w-2 rounded-full opacity-60 animate-ping", stat.dot)} />
          )}
          <span className={cn("relative h-2 w-2 rounded-full", stat.dot)} />
        </div>
        <h2 className="text-sm font-medium">Agent Activity Console</h2>
        <span className="text-label-sm text-muted-foreground">· {stat.caption}</span>
        <span className="ml-auto text-label-sm text-muted-foreground tabular-nums">
          {entries.length} {entries.length === 1 ? "event" : "events"}
        </span>
      </div>
      {banner === "unavailable" && (
        <div className="flex items-center gap-2 border-b border-red-500/20 bg-red-500/10 px-4 py-2 text-xs text-red-400">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" />
          <span className="flex-1">
            Agent activity unavailable — reload to retry.
          </span>
          <button
            type="button"
            onClick={reset}
            className="rounded border border-red-500/40 px-2 py-0.5 text-red-300 hover:bg-red-500/20"
          >
            Retry
          </button>
        </div>
      )}
      {banner === "error" && (
        <div className="flex items-center gap-2 border-b border-red-500/20 bg-red-500/10 px-4 py-2 text-xs text-red-400">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500 animate-pulse" />
          {error}
        </div>
      )}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto bg-background p-3 font-mono text-xs leading-relaxed"
      >
        {entries.length === 0 ? (
          <ConsoleEmptyState
            connecting={status === "connecting" || status === "retrying"}
            icon={Activity}
          />
        ) : (
          entries.map((entry: ActivityEntry, i) => {
            const colorClass =
              AGENT_COLORS[entry.agent] ?? "text-muted-foreground"
            return (
              <div key={`${entry.timestamp}-${i}`} className="flex gap-2">
                <span className="shrink-0 text-muted-foreground">
                  [{formatTime(entry.timestamp)}]
                </span>
                <span className={`shrink-0 font-semibold ${colorClass}`}>
                  {entry.agent}
                </span>
                <span className="text-foreground">{entry.message}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function ConsoleEmptyState({
  connecting,
  icon: Icon,
}: {
  connecting: boolean
  icon: LucideIcon
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 font-sans">
      <Icon className="h-6 w-6 text-muted-foreground/60" aria-hidden="true" />
      <p className="text-sm text-muted-foreground">
        {connecting ? "Waiting for the stream to come up…" : "No agent activity yet"}
      </p>
      {!connecting && (
        <p className="text-label-sm text-muted-foreground/70">
          Run an agent from the cards above to see live events here.
        </p>
      )}
    </div>
  )
}
