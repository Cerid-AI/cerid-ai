// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
    const minutes = Math.floor(seconds / 60)
    if (minutes < 60) return `${minutes}m ago`
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return `${hours}h ago`
    return `${Math.floor(hours / 24)}d ago`
  }, [now, timestamp])

  if (!label) return null

  return <span className="text-xs text-muted-foreground">Updated {label}</span>
}