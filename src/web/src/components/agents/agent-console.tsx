// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import { MCP_BASE } from "@/lib/api"

interface ActivityEntry {
  agent: string
  message: string
  level: string
  timestamp: number
}

const AGENT_COLORS: Record<string, string> = {
  QueryAgent: "text-blue-400",
  Decomposer: "text-violet-400",
  Assembler: "text-emerald-400",
  Verifier: "text-amber-400",
  Memory: "text-pink-400",
}

const MAX_ENTRIES = 100

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString("en-US", { hour12: false })
}

export default function AgentConsole() {
  const [entries, setEntries] = useState<ActivityEntry[]>([])
  const [connected, setConnected] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)
  const connectRef = useRef<() => void>(() => {})

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
    }

    const url = `${MCP_BASE}/agents/activity/stream`
    const es = new EventSource(url)
    esRef.current = es

    es.onopen = () => setConnected(true)

    es.onmessage = (event) => {
      try {
        const data: ActivityEntry = JSON.parse(event.data)
        setEntries((prev) => {
          const next = [...prev, data]
          return next.length > MAX_ENTRIES ? next.slice(-MAX_ENTRIES) : next
        })
      } catch {
        // ignore parse errors from keepalives
      }
    }

    es.onerror = () => {
      setConnected(false)
      es.close()
      // Reconnect after 3s
      setTimeout(() => connectRef.current(), 3000)
    }
  }, [])

  useEffect(() => {
    connectRef.current = connect
    connect()
    return () => {
      esRef.current?.close()
    }
  }, [connect])

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries])

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
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto bg-background p-3 font-mono text-xs leading-relaxed"
      >
        {entries.length === 0 && (
          <p className="text-muted-foreground">
            Waiting for agent activity...
          </p>
        )}
        {entries.map((entry, i) => {
          const colorClass =
            AGENT_COLORS[entry.agent] ?? "text-muted-foreground"
          return (
            <div key={i} className="flex gap-2">
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
