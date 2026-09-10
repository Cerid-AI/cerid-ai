// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Provider ids the setup wizard has configured, for the Service Health step.
 * An enabled local backend counts: the verification row is answerable without
 * a cloud key, so it must offer Re-check rather than "configure a provider
 * first". `selectedBackend` decides which local id is reported.
 */
export function configuredProviderIds(
  keys: Record<string, { valid: boolean }>,
  ollama: { enabled: boolean },
  selectedBackend: string | null,
): string[] {
  const ids = Object.entries(keys)
    .filter(([, k]) => k.valid)
    .map(([provider]) => provider)
  if (ollama.enabled) ids.push(selectedBackend === "quenchforge" ? "quenchforge" : "ollama")
  return ids
}
