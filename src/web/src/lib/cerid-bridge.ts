// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Types for the slice of the Electron desktop bridge (`window.cerid`) that the
 * shared web UI consumes for server-connection management. The full bridge is
 * defined in packages/desktop/src/preload/preload.ts.
 *
 * No `declare global` here on purpose — apple-detail.tsx already
 * augments `Window.cerid`, and a second differently-typed declaration would
 * conflict. Consumers read the bridge through `getConnectionBridge()`, which
 * narrows `window` with a local cast.
 */

export type ConnectionMode = "local" | "remote"

export interface ConnectionInfo {
  mode: ConnectionMode
  serverUrl: string
  hasApiKey: boolean
}

export interface ConnectionBridge {
  get(): Promise<ConnectionInfo>
  set(next: { mode: ConnectionMode; serverUrl: string; apiKey?: string }): Promise<ConnectionInfo>
  test(next: { serverUrl: string; apiKey?: string }): Promise<{ ok: boolean; detail: string }>
}

/** Returns the desktop connection bridge, or null in the browser build. */
export function getConnectionBridge(): ConnectionBridge | null {
  if (typeof window === "undefined") return null
  const cerid = (window as unknown as { cerid?: { connection?: ConnectionBridge } }).cerid
  return cerid?.connection ?? null
}
