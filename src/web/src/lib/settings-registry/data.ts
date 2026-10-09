// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type { SettingDef } from "./types"

const SYNCED = {
  scope: "synced" as const,
  display: "Every machine that shares this sync folder.",
}

export const DATA_DEFS: SettingDef[] = [
  {
    id: "data.trash.autoEmptyDays",
    category: "data",
    group: "trash",
    level: "core",
    label: "Empty the Trash after",
    helpText:
      "Days a deleted chat, memory or document stays in the Trash before it is erased for good. 0 keeps everything until you empty the Trash yourself.",
    scopeOfEffect: SYNCED,
    keywords: ["trash", "delete", "forget", "retention", "auto-empty", "FORGET_TRASH_DAYS", "forget_trash_days"],
    type: "number",
    default: 30,
    writer: { kind: "settings-patch", key: "forget_trash_days" },
  },
]
