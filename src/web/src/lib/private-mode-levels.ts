// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The Private Mode level contract. ``private-mode-levels.json`` is the single
 * source: the settings page, the settings registry, the chat toolbar and the
 * capability text render from it, ``docs/PRIVATE_MODE.md`` repeats it as a
 * table, and ``src/mcp/tests/test_private_mode_contract.py`` holds the doc,
 * the JSON and the server's ``PrivateModeRequest`` to the same ladder.
 */
import contract from "./private-mode-levels.json"

export type PrivateModeWithholdsKey =
  | "retrieval"
  | "memory"
  | "server"
  | "browser"
  | "audit"
  | "egress"

export type PrivateModeLevelNumber = 0 | 1 | 2 | 3 | 4

export interface PrivateModeLevel {
  level: PrivateModeLevelNumber
  /** The server's name for the level, as in ``PrivateModeRequest``. */
  short: string
  name: string
  label: string
  description: string
  withholds: Record<PrivateModeWithholdsKey, string>
}

export const PRIVATE_MODE_LEVELS: readonly PrivateModeLevel[] = contract.levels as PrivateModeLevel[]
export const PRIVATE_MODE_WITHHOLDS_COLUMNS: readonly { key: PrivateModeWithholdsKey; title: string }[] =
  contract.columns as { key: PrivateModeWithholdsKey; title: string }[]
export const PRIVATE_MODE_EGRESS_NOTE: string = contract.egress

export function privateModeLevel(level: number): PrivateModeLevel {
  return PRIVATE_MODE_LEVELS[level] ?? PRIVATE_MODE_LEVELS[0]
}
