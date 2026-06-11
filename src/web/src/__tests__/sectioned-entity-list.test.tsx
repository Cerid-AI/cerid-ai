// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import {
  SectionedEntityListPalette,
  SectionedEntityListWiki,
} from "@/components/shared/sectioned-entity-list"
import type { DomainSection } from "@/lib/graph/organize"
import type { EntitySummary } from "@/lib/types/wiki"

function makeEntity(slug: string, domain: string | null = null): EntitySummary {
  return {
    slug,
    name: slug.charAt(0).toUpperCase() + slug.slice(1),
    entity_type: "ORG",
    summary_preview: null,
    mention_count: 0,
    recent_activity_score: 0,
    last_updated_at: null,
    primary_domain: domain,
  }
}

function makeSection(
  domain: string | null,
  slugs: string[],
  overflow = 0,
): DomainSection {
  return {
    domain,
    label: domain === null ? "Other" : domain.charAt(0).toUpperCase() + domain.slice(1),
    icon: null,
    count: slugs.length + overflow,
    entities: slugs.map((s) => makeEntity(s, domain)),
    overflow,
  }
}

// ---------------------------------------------------------------------------
// SectionedEntityListPalette — palette variant
// ---------------------------------------------------------------------------

describe("SectionedEntityListPalette — rendering", () => {
  it("renders entity names as options", () => {
    const sections = [makeSection("research", ["tesla", "spacex"])]
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="test-listbox"
        headerless={true}
      />,
    )
    expect(screen.getByText("Tesla")).toBeTruthy()
    expect(screen.getByText("Spacex")).toBeTruthy()
  })

  it("renders section header when not headerless", () => {
    const sections = [
      makeSection("research", ["a"]),
      makeSection("coding", ["b"]),
    ]
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="test-listbox"
        headerless={false}
      />,
    )
    expect(screen.getByText("Research")).toBeTruthy()
    expect(screen.getByText("Coding")).toBeTruthy()
  })

  it("does NOT render section headers when headerless=true", () => {
    const sections = [makeSection("research", ["a", "b"])]
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="test-listbox"
        headerless={true}
      />,
    )
    // "Research" label should NOT appear as a section header
    expect(screen.queryByText("Research")).toBeNull()
  })

  it("marks highlighted option with aria-selected=true", () => {
    const sections = [makeSection("research", ["a", "b"])]
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={1}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="test-listbox"
        headerless={true}
      />,
    )
    const options = screen.getAllByRole("option")
    expect(options[0]).toHaveAttribute("aria-selected", "false")
    expect(options[1]).toHaveAttribute("aria-selected", "true")
  })

  it("calls onPick when an entity option is clicked", async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const sections = [makeSection("research", ["tesla"])]
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={onPick}
        onNavigateToDomain={vi.fn()}
        listboxId="test-listbox"
        headerless={true}
      />,
    )
    // Click the option element that contains the entity name
    const option = screen.getByRole("option", { name: /Tesla/ })
    await user.click(option)
    expect(onPick).toHaveBeenCalledWith("tesla")
  })

  it("calls onHighlight on mouse enter over an option", async () => {
    const user = userEvent.setup()
    const onHighlight = vi.fn()
    const sections = [makeSection("research", ["a", "b"])]
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={0}
        onHighlight={onHighlight}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="test-listbox"
        headerless={true}
      />,
    )
    const options = screen.getAllByRole("option")
    await user.hover(options[1])
    expect(onHighlight).toHaveBeenCalledWith(1)
  })

  it("renders overflow row when section.overflow > 0", () => {
    const section = makeSection("research", ["a"], 3)
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={[section]}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="test-listbox"
        headerless={false}
      />,
    )
    expect(screen.getByText(/3 more in Research/)).toBeTruthy()
  })

  it("overflow row calls onNavigateToDomain on click", async () => {
    const user = userEvent.setup()
    const onNav = vi.fn()
    const section = makeSection("research", ["a"], 2)
    render(
      <SectionedEntityListPalette
        variant="palette"
        sections={[section, makeSection("coding", ["b"])]}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={onNav}
        listboxId="test-listbox"
        headerless={false}
      />,
    )
    await user.click(screen.getByText(/2 more in Research/))
    expect(onNav).toHaveBeenCalledWith("research")
  })
})

describe("SectionedEntityListPalette — a11y (D.3)", () => {
  it("is axe-clean in headerless single-section state", async () => {
    const sections = [makeSection("research", ["a", "b"])]
    const { container } = render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="axe-test-listbox"
        headerless={true}
      />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("is axe-clean in multi-section state", async () => {
    const sections = [
      makeSection("research", ["a"]),
      makeSection("coding", ["b"]),
    ]
    const { container } = render(
      <SectionedEntityListPalette
        variant="palette"
        sections={sections}
        highlightIndex={0}
        onHighlight={vi.fn()}
        onPick={vi.fn()}
        onNavigateToDomain={vi.fn()}
        listboxId="axe-test-listbox-2"
        headerless={false}
      />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// SectionedEntityListWiki — wiki variant
// ---------------------------------------------------------------------------

describe("SectionedEntityListWiki — rendering", () => {
  it("renders entity list items", () => {
    const sections = [makeSection("research", ["tesla", "spacex"])]
    render(
      <SectionedEntityListWiki
        variant="wiki"
        sections={sections}
        headerless={true}
        selectedSlug={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText("Tesla")).toBeTruthy()
    expect(screen.getByText("Spacex")).toBeTruthy()
  })

  it("renders section headers when not headerless", () => {
    const sections = [
      makeSection("research", ["a"]),
      makeSection("coding", ["b"]),
    ]
    render(
      <SectionedEntityListWiki
        variant="wiki"
        sections={sections}
        headerless={false}
        selectedSlug={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText("Research")).toBeTruthy()
    expect(screen.getByText("Coding")).toBeTruthy()
  })

  it("does not render headers when headerless=true", () => {
    const sections = [makeSection("research", ["a"])]
    render(
      <SectionedEntityListWiki
        variant="wiki"
        sections={sections}
        headerless={true}
        selectedSlug={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.queryByText("Research")).toBeNull()
  })

  it("calls onSelect when entity list item is activated", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const sections = [makeSection("research", ["tesla"])]
    render(
      <SectionedEntityListWiki
        variant="wiki"
        sections={sections}
        headerless={true}
        selectedSlug={null}
        onSelect={onSelect}
      />,
    )
    await user.click(screen.getByText("Tesla"))
    expect(onSelect).toHaveBeenCalledWith("tesla")
  })
})

describe("SectionedEntityListWiki — a11y (D.3)", () => {
  it("is axe-clean in headerless state", async () => {
    const sections = [makeSection("research", ["a"])]
    const { container } = render(
      <SectionedEntityListWiki
        variant="wiki"
        sections={sections}
        headerless={true}
        selectedSlug={null}
        onSelect={vi.fn()}
      />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("is axe-clean in multi-section state", async () => {
    const sections = [
      makeSection("research", ["a"]),
      makeSection("coding", ["b"]),
    ]
    const { container } = render(
      <SectionedEntityListWiki
        variant="wiki"
        sections={sections}
        headerless={false}
        selectedSlug={null}
        onSelect={vi.fn()}
      />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
