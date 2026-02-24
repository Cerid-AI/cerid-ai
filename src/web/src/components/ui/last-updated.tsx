import { useState, useEffect } from "react"

interface LastUpdatedProps {
  timestamp: number | undefined
}

export function LastUpdated({ timestamp }: LastUpdatedProps) {
  const [, setTick] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 10_000)
    return () => clearInterval(id)
  }, [])

  if (!timestamp) return null

  const seconds = Math.floor((Date.now() - timestamp) / 1000)
  let label: string
  if (seconds < 5) label = "just now"
  else if (seconds < 60) label = `${seconds}s ago`
  else label = `${Math.floor(seconds / 60)}m ago`

  return <span className="text-xs text-muted-foreground">Updated {label}</span>
}
