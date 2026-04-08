// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useRef, useCallback } from "react"
import { MCP_BASE } from "@/lib/api/common"

export interface AgentEvent {
  id: string
  agent: string
  message: string
  level: string // info | success | warning | error
  timestamp: number
  metadata: Record<string, unknown>
}

const MAX_EVENTS = 200
const THROTTLE_MS = 1000 // max 1 message per agent per second

/**
 * SSE-based hook for the Agent Communication Console.
 *
 * Connects to /agent-console/stream, buffers events, and exposes
 * filter/clear controls.  Reconnects automatically on disconnect.
 */
export function useAgentConsole(enabled: boolean) {
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const lastEmitRef = useRef<Record<string, number>>({})
  const eventSourceRef = useRef<EventSource | null>(null)
  const bufferRef = useRef<AgentEvent[]>([])
  const rafRef = useRef<number | null>(null)

  // Flush buffered events on the next animation frame
  const flushBuffer = useCallback(() => {
    if (bufferRef.current.length === 0) return
    const batch = bufferRef.current.splice(0)
    setEvents((prev) => {
      const next = [...prev, ...batch]
      return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next
    })
    setUnreadCount((c) => c + batch.length)
    rafRef.current = null
  }, [])

  // Throttle check: skip if same agent emitted within THROTTLE_MS
  const shouldAccept = useCallback((agent: string): boolean => {
    const now = Date.now()
    const last = lastEmitRef.current[agent] ?? 0
    if (now - last < THROTTLE_MS) return false
    lastEmitRef.current[agent] = now
    return true
  }, [])

  useEffect(() => {
    if (!enabled) {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      return
    }

    // Fetch recent events for initial hydration
    fetch(`${MCP_BASE}/agent-console/recent?count=50`)
      .then((r) => r.json())
      .then((data) => {
        if (data.events?.length) {
          const parsed: AgentEvent[] = data.events.reverse().map((e: Record<string, unknown>) => ({
            id: e.id as string,
            agent: e.agent as string,
            message: e.message as string,
            level: (e.level as string) || "info",
            timestamp: e.timestamp as number,
            metadata: (e.metadata ?? {}) as Record<string, unknown>,
          }))
          setEvents(parsed)
        }
      })
      .catch(() => { /* initial load failure is non-critical */ })

    const connect = () => {
      const es = new EventSource(`${MCP_BASE}/agent-console/stream`)
      eventSourceRef.current = es

      es.onopen = () => setConnected(true)

      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as AgentEvent
          if (!data.agent || !data.message) return
          if (!shouldAccept(data.agent)) return

          bufferRef.current.push(data)
          if (rafRef.current === null) {
            rafRef.current = requestAnimationFrame(flushBuffer)
          }
        } catch { /* ignore unparseable */ }
      }

      es.onerror = () => {
        setConnected(false)
        es.close()
        // Reconnect after 3 seconds
        setTimeout(connect, 3000)
      }
    }

    connect()

    return () => {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      setConnected(false)
    }
  }, [enabled, shouldAccept, flushBuffer])

  const clearEvents = useCallback(() => {
    setEvents([])
    setUnreadCount(0)
    fetch(`${MCP_BASE}/agent-console/clear`, { method: "DELETE" }).catch(() => {})
  }, [])

  const resetUnread = useCallback(() => setUnreadCount(0), [])

  return { events, connected, unreadCount, clearEvents, resetUnread }
}
