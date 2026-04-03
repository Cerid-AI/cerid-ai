// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useMemo } from "react"

interface LastUpdatedProps {
  timestamp: number | undefined
}

export function LastUpdated({ timestamp }: LastUpdatedProps) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 10_000)
    return () => clearInterval(id)
  }, [])

  const label = useMemo(() => {
    if (!timestamp) return null
    const seconds = Math.floor((now - timestamp) / 1000)
    if (seconds < 5) return "just now"
    if (seconds < 60) return `${seconds}s ago`
    return `${Math.floor(seconds / 60)}m ago`
  }, [now, timestamp])

  if (!label) return null

  return <span className="text-xs text-muted-foreground">Updated {label}</span>
}