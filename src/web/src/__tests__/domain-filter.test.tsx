// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { DomainFilter } from "@/components/kb/domain-filter"
import { DomainBadge } from "@/components/ui/domain-badge"
import { DOMAINS } from "@/lib/types"

describe("DomainFilter", () => {
  it("renders a button for each domain", () => {
    render(<DomainFilter activeDomains={new Set()} onToggle={vi.fn()} />)
    for (const domain of DOMAINS) {
      expect(screen.getByText(domain)).toBeInTheDocument()
    }
  })

  it("marks active domains with aria-pressed true", () => {
    render(
      <DomainFilter activeDomains={new Set(["coding", "finance"])} onToggle={vi.fn()} />,
    )
    expect(screen.getByText("coding")).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByText("finance")).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByText("general")).toHaveAttribute("aria-pressed", "false")
  })

  it("calls onToggle with the domain when a badge is clicked", async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(<DomainFilter activeDomains={new Set()} onToggle={onToggle} />)
    await user.click(screen.getByText("projects"))
    expect(onToggle).toHaveBeenCalledWith("projects")
  })

  it("calls onToggle on Enter key press", async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(<DomainFilter activeDomains={new Set()} onToggle={onToggle} />)
    const badge = screen.getByText("coding")
    badge.focus()
    await user.keyboard("{Enter}")
    expect(onToggle).toHaveBeenCalledWith("coding")
  })

  it("renders all domains with capitalize class", () => {
    render(<DomainFilter activeDomains={new Set()} onToggle={vi.fn()} />)
    for (const domain of DOMAINS) {
      expect(screen.getByText(domain).className).toContain("capitalize")
    }
  })

  it("inactive chips have inline domain CSS var color style", () => {
    render(<DomainFilter activeDomains={new Set()} onToggle={vi.fn()} />)
    // Any inactive chip should carry a style with var(--color-domain-*)
    const badge = screen.getByText("coding")
    expect(badge.style.color).toMatch(/var\(--color-domain-/)
  })

  it("active chips do not have the domain color inline style", () => {
    render(<DomainFilter activeDomains={new Set(["coding"])} onToggle={vi.fn()} />)
    const activeBadge = screen.getByText("coding")
    // Active chips use the default Badge "default" variant styling, no inline color
    expect(activeBadge.style.color).toBe("")
  })
})

describe("DomainBadge", () => {
  it("renders the domain name with capitalize class", () => {
    render(<DomainBadge domain="finance" />)
    const badge = screen.getByText("finance")
    expect(badge).toBeInTheDocument()
    expect(badge.className).toContain("capitalize")
  })

  it("renders with outline variant", () => {
    render(<DomainBadge domain="coding" />)
    const badge = screen.getByText("coding")
    expect(badge).toHaveAttribute("data-variant", "outline")
  })

  it("uses token-routed inline style color (no hardcoded hex)", () => {
    render(<DomainBadge domain="research" />)
    const badge = screen.getByText("research")
    // Must use CSS var, not a hardcoded class or hex
    expect(badge.style.color).toMatch(/var\(--color-domain-/)
  })

  it("unknown runtime-minted domain gets a consistent slot color", () => {
    render(<DomainBadge domain="canary_client_domain" />)
    const badge = screen.getByText("canary_client_domain")
    // Should still render with domain CSS var (hash slot)
    expect(badge.style.color).toMatch(/var\(--color-domain-/)
  })
})
