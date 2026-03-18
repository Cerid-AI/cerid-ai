// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { type FC } from 'react'
import { TradingKPIs } from './trading-kpis'
import { SessionDetail } from './session-detail'
import { useTradingData, type TradingData, type TradingSession } from '@/hooks/use-trading-data'

interface TradingPaneProps {
  data?: TradingData
  loading?: boolean
}

const TradingPaneInner: FC<TradingPaneProps> = ({ data, loading }) => {
  if (loading) {
    return <div className="flex items-center justify-center h-64 text-zinc-400">Loading trading data...</div>
  }

  const agg = data?.aggregate ?? {}
  const sessions = data?.sessions ?? []

  return (
    <div className="p-6">
      <h2 className="text-xl font-bold text-zinc-100 mb-4">Trading Agent</h2>
      <TradingKPIs
        totalPnl={agg.total_pnl ?? 0}
        dailyPnl={agg.daily_pnl ?? 0}
        sharpe={agg.sharpe ?? 0}
        maxDrawdown={agg.max_drawdown ?? 0}
        activeSessions={sessions.length}
      />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {sessions.map(s => (
          <SessionDetail
            key={s.name}
            name={s.name}
            pnl={s.pnl ?? 0}
            positions={s.open_positions ?? 0}
            signals={s.signals_today ?? 0}
            status={s.status ?? 'unknown'}
          />
        ))}
      </div>
    </div>
  )
}

/**
 * Default export wraps the inner pane with the data-fetching hook,
 * so it can be lazy-loaded by App.tsx.
 */
export default function TradingPane() {
  const { data, loading } = useTradingData()
  return <TradingPaneInner data={data} loading={loading} />
}
