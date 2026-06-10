// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Public/community edition: the Plan & Billing surface is a static stub.
// Licensing, upgrades, and checkout are handled on cerid.ai — this
// self-hosted build never talks to a payment provider.

import type { SettingDef } from "./types"

export const PLAN_DEFS: SettingDef[] = [
  {
    id: "plan.tier.current",
    category: "plan",
    group: "tier",
    level: "core",
    label: "Current plan",
    helpText:
      "Your active plan tier. Community is free and self-hosted with the full core feature set. " +
      "Pro and Enterprise add scheduled automations, Smart RAG customization, and multi-user features.",
    scopeOfEffect: { scope: "server", display: "Applies to this server instance — all sessions." },
    keywords: ["plan", "tier", "community", "pro", "enterprise", "billing", "upgrade", "Pro"],
    type: "display",
    writer: { kind: "readonly" },
  },
  {
    id: "plan.tier.manage",
    category: "plan",
    group: "tier",
    level: "core",
    label: "Manage plan",
    helpText:
      "Upgrades and license keys are managed on cerid.ai. A purchased license activates Pro or " +
      "Enterprise features on this server.",
    scopeOfEffect: { scope: "server", display: "Applies to this server instance — all sessions." },
    keywords: ["upgrade", "manage", "subscription", "license", "license key", "pricing"],
    type: "display",
    writer: { kind: "readonly" },
  },
]
