// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type { SettingDef } from "./types"

export const PRIVACY_DEFS: SettingDef[] = [
  // ── Private Mode L0–L4 ─────────────────────────────────────────────────────
  {
    id: "privacy.mode.level",
    category: "privacy",
    group: "mode",
    level: "core",
    label: "Private Mode",
    helpText:
      "Controls how much context persists as you chat. " +
      "L0 = off (standard). " +
      "L1 = skip saves & sync. " +
      "L2 = also skip KB injection. " +
      "L3 = also no logging — nothing reaches Redis. " +
      "L4 = full ephemeral — session erased on tab close.",
    scopeOfEffect: {
      scope: "server",
      display: "Global for this server — all tabs and sessions.",
    },
    keywords: [
      "private", "privacy", "mode", "level", "logging", "KB", "ephemeral", "session",
      "wipe", "no logging", "Essentials",
    ],
    type: "enum",
    options: [
      { value: 0, label: "L0 — Off", helpText: "Standard behaviour. Conversations persist to server and local cache." },
      { value: 1, label: "L1 — Skip saves & sync", helpText: "Don't save this conversation; don't sync to other devices." },
      { value: 2, label: "L2 — Also skip KB injection", helpText: "Also bypass KB injection — the model sees only what you type." },
      { value: 3, label: "L3 — Also no logging", helpText: "Also skip audit log entries. Nothing reaches Redis." },
      { value: 4, label: "L4 — Full ephemeral", helpText: "One-shot per tab. Session erased on tab close — even the audit log is bypassed. Requires confirmation." },
    ],
    default: 0,
    writer: { kind: "endpoint", method: "POST", path: "/settings/private-mode" },
    mirrors: ["chat-toolbar"],
    writtenBy: "Chat toolbar",
  },
  // ── Encryption ─────────────────────────────────────────────────────────────
  {
    id: "privacy.data.encryption",
    category: "privacy",
    group: "data",
    level: "core",
    label: "Encryption at rest",
    helpText: "Whether Cerid encrypts stored KB data. Controlled by CERID_ENCRYPTION in .env — restart required.",
    scopeOfEffect: {
      scope: "env",
      display: "Read-only here — set CERID_ENCRYPTION in .env and restart.",
    },
    keywords: ["encryption", "encrypt", "at rest", "security", "CERID_ENCRYPTION", "System"],
    type: "display",
    writer: { kind: "env", envVar: "CERID_ENCRYPTION" },
  },
  // ── Anonymization / Audit (read-only env rows) ──────────────────────────────
  {
    id: "privacy.data.anonymizeEmailHeaders",
    category: "privacy",
    group: "data",
    level: "advanced",
    label: "Anonymize email headers",
    helpText: "Strip personally-identifying email headers during ingestion. Controlled by CERID_ANONYMIZE_EMAIL_HEADERS.",
    scopeOfEffect: {
      scope: "env",
      display: "Read-only here — set CERID_ANONYMIZE_EMAIL_HEADERS in .env and restart.",
    },
    keywords: ["anonymize", "email", "headers", "PII", "CERID_ANONYMIZE_EMAIL_HEADERS", "Governance"],
    type: "display",
    writer: { kind: "env", envVar: "CERID_ANONYMIZE_EMAIL_HEADERS" },
  },
  {
    id: "privacy.data.auditRetentionDays",
    category: "privacy",
    group: "data",
    level: "advanced",
    label: "Audit log retention (days)",
    helpText: "How many days audit log entries are retained. Controlled by AUDIT_RETENTION_DAYS.",
    scopeOfEffect: {
      scope: "env",
      display: "Read-only here — set AUDIT_RETENTION_DAYS in .env and restart.",
    },
    keywords: ["audit", "retention", "log", "days", "AUDIT_RETENTION_DAYS", "Governance"],
    type: "display",
    writer: { kind: "env", envVar: "AUDIT_RETENTION_DAYS" },
  },
]
