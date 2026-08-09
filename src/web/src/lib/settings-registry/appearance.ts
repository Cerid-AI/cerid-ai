// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type { SettingDef } from "./types"

const DEVICE_SCOPE = { scope: "device" as const, display: "This device only" }

export const APPEARANCE_DEFS: SettingDef[] = [
  {
    id: "appearance.theme.mode",
    category: "appearance",
    group: "theme",
    level: "core",
    label: "Theme",
    helpText:
      "Light, Dark, or follow the operating system. System tracks your OS setting live.",
    scopeOfEffect: DEVICE_SCOPE,
    keywords: ["dark mode", "light mode", "night", "color scheme", "system theme"],
    type: "enum",
    options: [
      { value: "light", label: "Light" },
      { value: "dark", label: "Dark" },
      { value: "system", label: "System", helpText: "Follow the OS appearance setting" },
    ],
    default: "system",
    writer: { kind: "local", storageKey: "cerid-theme" },
    writtenBy: "the sidebar theme button",
    mirrors: ["sidebar-footer"],
  },
  {
    id: "appearance.density.mode",
    category: "appearance",
    group: "density",
    level: "core",
    label: "Density",
    helpText:
      "Compact tightens spacing on settings and list surfaces. Other panes adopt density in a later release.",
    scopeOfEffect: DEVICE_SCOPE,
    keywords: ["compact", "comfortable", "spacing", "row height"],
    type: "enum",
    options: [
      { value: "comfortable", label: "Comfortable" },
      { value: "compact", label: "Compact" },
    ],
    default: "comfortable",
    writer: { kind: "local", storageKey: "cerid-density" },
  },
  {
    id: "appearance.motion.mode",
    category: "appearance",
    group: "motion",
    level: "core",
    label: "Reduce motion",
    helpText:
      "System follows your OS reduced-motion preference. Reduce disables animations and transitions in Cerid regardless of the OS setting.",
    scopeOfEffect: DEVICE_SCOPE,
    keywords: ["animations", "reduced motion", "accessibility", "transitions", "vestibular"],
    type: "enum",
    options: [
      { value: "system", label: "System", helpText: "Follow prefers-reduced-motion" },
      { value: "reduce", label: "Reduce", helpText: "Always minimize motion" },
    ],
    default: "system",
    writer: { kind: "local", storageKey: "cerid-motion" },
  },
]
