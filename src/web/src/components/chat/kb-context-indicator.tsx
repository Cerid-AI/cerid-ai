// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Compat shim — the standalone KB context indicator was merged into
 * <SourceAttribution variant="badge" /> as part of Phase 7 (C-P1.4).
 * Both the tooltip-badge and the collapsible-card-list shapes now live on
 * one component so the prop contract is normalized and they cannot drift.
 *
 * This shim is preserved so consumers we cannot edit in this phase (e.g.
 * `message-bubble.tsx`, owned by a parallel Phase 4 agent) keep working.
 * Once that file lands its Phase 4 changes, switch its callsite to
 * `<SourceAttribution variant="badge" sources={...} />` and delete this
 * shim.
 */

import { SourceAttribution } from "./source-attribution"
import type { SourceRef } from "@/lib/types"

export function KBContextIndicator({ sources }: { sources?: SourceRef[] }) {
  if (!sources?.length) return null
  return <SourceAttribution sources={sources} variant="badge" />
}
