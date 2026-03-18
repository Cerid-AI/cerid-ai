// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { type FC } from 'react'

interface SessionDetailProps {
  name: string
  pnl: number
  positions: number
  signals: number
  status: string
}

export const SessionDetail: FC<SessionDetailProps> = ({
  name, pnl, positions, signals, status,
}) => (
  <div className="bg-zinc-800 rounded-lg p-4 border border-zinc-700">
    <div className="flex justify-between items-center mb-2">
      <h3 className="text-lg font-semibold text-zinc-100">{name}</h3>
      <span className={`text-xs px-2 py-1 rounded ${
        status === 'running' ? 'bg-green-900 text-green-300' : 'bg-zinc-700 text-zinc-400'
      }`}>{status}</span>
    </div>
    <div className="grid grid-cols-3 gap-2 text-sm">
      <div><span className="text-zinc-400">PnL:</span> <span className={pnl >= 0 ? 'text-green-400' : 'text-red-400'}>${pnl.toFixed(2)}</span></div>
      <div><span className="text-zinc-400">Positions:</span> {positions}</div>
      <div><span className="text-zinc-400">Signals:</span> {signals}</div>
    </div>
  </div>
)
