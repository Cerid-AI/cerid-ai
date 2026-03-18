// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { type FC } from 'react'

interface TradingKPIsProps {
  totalPnl: number
  dailyPnl: number
  sharpe: number
  maxDrawdown: number
  activeSessions: number
}

export const TradingKPIs: FC<TradingKPIsProps> = ({
  totalPnl, dailyPnl, sharpe, maxDrawdown, activeSessions,
}) => (
  <div className="grid grid-cols-5 gap-4 mb-6">
    {[
      { label: 'Total PnL', value: `$${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? 'text-green-400' : 'text-red-400' },
      { label: 'Daily PnL', value: `$${dailyPnl.toFixed(2)}`, color: dailyPnl >= 0 ? 'text-green-400' : 'text-red-400' },
      { label: 'Sharpe (7d)', value: sharpe.toFixed(2), color: sharpe >= 1.5 ? 'text-green-400' : 'text-yellow-400' },
      { label: 'Max Drawdown', value: `${maxDrawdown.toFixed(1)}%`, color: maxDrawdown < 5 ? 'text-green-400' : 'text-red-400' },
      { label: 'Active Sessions', value: String(activeSessions), color: 'text-blue-400' },
    ].map(kpi => (
      <div key={kpi.label} className="bg-zinc-800 rounded-lg p-4">
        <div className="text-zinc-400 text-xs uppercase">{kpi.label}</div>
        <div className={`text-2xl font-bold ${kpi.color}`}>{kpi.value}</div>
      </div>
    ))}
  </div>
)
