// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Cerid AI service worker. Single responsibility for the Phase 4b
 * scope: seed default storage values on install. The popup handles
 * everything else; we don't run a persistent background listener.
 */

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get("cerid_base_url")
  if (!stored.cerid_base_url) {
    await chrome.storage.local.set({ cerid_base_url: "http://localhost:8888" })
  }
})
