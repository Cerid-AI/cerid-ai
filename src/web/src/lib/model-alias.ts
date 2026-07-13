// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Cross-backend model-name matching.
 *
 * Local inference gateways alias the same model differently: Ollama uses
 * colon tags (`llama3.1:8b`) while Quenchforge serves bare dash aliases
 * (`llama3.1-8b`), optionally carrying a GGUF quant suffix (`.Q8_0`).
 * Raw string comparison across those forms silently fails, so every
 * "is this model installed/served?" check must normalize first.
 */

// GGUF quant suffix, case-insensitive: ".Q8_0", "-q4_K_M", "_Q5_0", etc.
const QUANT_SUFFIX_RE = /[._-]q\d+(?:_[a-z0-9]+)*$/i

/**
 * Normalize a model name for cross-matching recommended (Ollama colon-tag,
 * e.g. `llama3.2:3b`) against installed (local Quenchforge dash-alias, e.g.
 * `llama3.2-3b`) forms. Collapses `:`/`-` to a single separator and strips
 * any trailing GGUF quant suffix so quant variants match the base model.
 */
export function normalizeModelId(name: string): string {
  return name.replace(QUANT_SUFFIX_RE, "").replace(/[:-]/g, "-").toLowerCase()
}

/**
 * Find the installed/served alias corresponding to a recommended model id,
 * or `null` when the model isn't installed. Returns the *installed*
 * spelling so callers can address the backend by a name it actually serves
 * (e.g. recommended `llama3.1:8b` resolves to served `llama3.1-8b`).
 */
export function findInstalledModel(recommendedId: string, installed: string[]): string | null {
  const target = normalizeModelId(recommendedId)
  return (
    installed.find((om) => {
      const norm = normalizeModelId(om)
      return norm === target || norm.startsWith(`${target}-`)
    }) ?? null
  )
}

/** True when an installed model corresponds to a recommended model id. */
export function isModelInstalled(recommendedId: string, installed: string[]): boolean {
  return findInstalledModel(recommendedId, installed) !== null
}
