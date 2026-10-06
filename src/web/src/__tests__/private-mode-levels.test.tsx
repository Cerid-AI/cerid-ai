// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The settings page, the settings registry and the capability text render
 * the Private Mode levels from the shared contract (private-mode-levels.json),
 * so what the page says a level withholds is what the contract says.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import PrivacyCategory from "@/components/settings/categories/privacy"
import { CAPABILITY_DESCRIPTIONS } from "@/lib/capability-descriptions"
import { getDef } from "@/lib/settings-registry"
import {
  PRIVATE_MODE_EGRESS_NOTE,
  PRIVATE_MODE_LEVELS,
  PRIVATE_MODE_WITHHOLDS_COLUMNS,
} from "@/lib/private-mode-levels"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"

const settings: ServerSettings = {
  feature_tier: "community",
  feature_flags: {},
  categorize_mode: "smart",
  chunk_max_tokens: 512,
  chunk_overlap: 64,
  cost_sensitivity: "medium",
  enable_encryption: false,
  enable_feedback_loop: true,
  enable_hallucination_check: true,
  enable_memory_extraction: true,
  enable_model_router: false,
  hallucination_threshold: 0.7,
  enable_auto_inject: true,
  auto_inject_threshold: 0.55,
  auto_inject_max: 3,
  domains: [],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "",
  machine_id: "test",
  version: "1.0.0",
  sensitive_domain_retrieval: false,
}

const props: SettingsCategoryPageProps = {
  settings,
  patch: vi.fn().mockResolvedValue({ ok: true }),
  onRefresh: vi.fn(),
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function ok(data: unknown) {
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
    if (url.includes("/settings/egress")) return ok({ egress: [] })
    if (url.includes("/settings/private-mode")) return ok({ level: 0 })
    return ok({})
  }))
})

describe("Private Mode level copy comes from the shared contract", () => {
  it("the settings page shows every level's label and description", () => {
    render(<PrivacyCategory {...props} />, { wrapper })
    for (const level of PRIVATE_MODE_LEVELS) {
      expect(screen.getAllByText(level.label).length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText(level.description)).toBeInTheDocument()
    }
    expect(screen.getByText(PRIVATE_MODE_EGRESS_NOTE)).toBeInTheDocument()
  })

  it("selecting a level lists what it withholds on every dimension", async () => {
    const user = userEvent.setup()
    render(<PrivacyCategory {...props} />, { wrapper })
    const l3 = PRIVATE_MODE_LEVELS[3]
    await user.click(screen.getByRole("button", { name: new RegExp(l3.label) }))
    for (const column of PRIVATE_MODE_WITHHOLDS_COLUMNS) {
      expect(await screen.findByText(l3.withholds[column.key])).toBeInTheDocument()
    }
  })

  it("the settings registry options are the contract's levels", () => {
    const def = getDef("privacy.mode.level")!
    expect(def.options).toEqual(
      PRIVATE_MODE_LEVELS.map((l) => ({ value: l.level, label: l.label, helpText: l.description })),
    )
    for (const level of PRIVATE_MODE_LEVELS) expect(def.helpText).toContain(`L${level.level} = ${level.name}`)
    expect(def.helpText).toContain(PRIVATE_MODE_EGRESS_NOTE)
  })

  it("the capability text names every level and says egress is not blocked", () => {
    const text = CAPABILITY_DESCRIPTIONS.private_mode.description
    for (const level of PRIVATE_MODE_LEVELS.slice(1)) expect(text).toContain(`${level.name} (L${level.level})`)
    expect(text).toContain(PRIVATE_MODE_EGRESS_NOTE)
    expect(CAPABILITY_DESCRIPTIONS.private_mode.tier).toBeUndefined()
  })
})
