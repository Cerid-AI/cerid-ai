// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef } from "react"
import {
  useAgentActivityStream,
  type ActivityEntry,
} from "@/hooks/use-agent-activity-stream"

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

interface AgentConsoleProps {
  /**
   * When false, the SSE connection is torn down. The parent (Agents tab
   * host) flips this on unmount so the stream doesn't leak across tab
   * changes — fixes the "keeps polling after user navigates away" bug.
   */
  enabled?: boolean
}

export default function AgentConsole({ enabled = true }: AgentConsoleProps) {
  const { entries, status, error, reset } = useAgentActivityStream({ enabled })
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries])

  const connected = status === "open"
  const banner = status === "unavailable" ? "unavailable" : error ? "error" : null

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <div
          className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-red-500"}`}
        />
        <h2 className="text-sm font-medium">Agent Activity Console</h2>
        <span className="ml-auto text-xs text-muted-foreground">
          {entries.length} events
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
        {entries.length === 0 && (
          <p className="text-muted-foreground">
            Waiting for agent activity...
          </p>
        )}
        {entries.map((entry: ActivityEntry, i) => {
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
        })}
      </div>
    </div>
  )
}
