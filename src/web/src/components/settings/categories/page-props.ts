// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type { ProviderCredits, ServerSettings, SettingsUpdate } from "@/lib/types"

/** Outcome of a shell `patch()` call. Never rejects — failures roll the
    optimistic update back, surface in the shell's persistent save-failed
    Alert, and return `{ ok: false }` so callers can add inline context. */
export type PatchResult = { ok: true } | { ok: false; error: string }

/**
 * Props every settings category page receives from the shell
 * (`settings-pane.tsx`). Pages own their group layout (Cards +
 * `SettingRow`s + at most one `AdvancedDisclosure` per group); the shell
 * owns the sidebar, search, header, U-1 mode toggle, and the
 * recommendations banner.
 */
export interface SettingsCategoryPageProps {
  /** Loaded server settings (the shell renders skeleton/error before this exists). */
  settings: ServerSettings
  /** Optimistic PATCH /settings with rollback; save failures surface in the shell Alert. */
  patch: (update: SettingsUpdate) => Promise<PatchResult>
  /** Provider credits poll (Models page). */
  credits?: ProviderCredits
  /** Re-fetch the settings object (e.g. after an endpoint-writer action). */
  onRefresh: () => void | Promise<void>
}
