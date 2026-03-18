// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from 'react'

interface TradingData {
  aggregate: Record<string, unknown>
  sessions: Array<Record<string, unknown>>
}

export function useTradingData(refreshInterval = 10000) {
  const [data, setData] = useState<TradingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [aggRes, sessRes] = await Promise.all([
        fetch('/api/trading/aggregate/portfolio'),
        fetch('/api/trading/sessions'),
      ])
      const aggregate = aggRes.ok ? await aggRes.json() : {}
      const sessions = sessRes.ok ? await sessRes.json() : []
      setData({ aggregate, sessions })
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch trading data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, refreshInterval)
    return () => clearInterval(interval)
  }, [fetchData, refreshInterval])

  return { data, loading, error }
}
