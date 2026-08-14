// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Builds per-Leiden-level super-node sets from the level-0 community hulls
// (data.communities) + the community hierarchy (/graph/community-hierarchy).
// Each level is represented as CommunityHull[] so the existing super-node
// overlay renders any level unchanged. Higher-level anchors are the
// count-weighted centroid of descendant level-0 anchors; hulls are the union
// of descendant hulls (used only as the click→zoom region).

import type { CommunityHull } from "@/lib/api/graph-map"
import type { CommunityHierarchy } from "@/lib/api/community-hierarchy"
import { aggregateCommunityEdges, type SuperEdge } from "./community-supernodes"

export interface AncestorIndex {
  levelOf: (id: string) => number
  ancestorAt: (id: string, level: number) => string
  childrenOf: (id: string) => string[]
  maxLevel: number
}

export function buildAncestorIndex(hierarchy: CommunityHierarchy): AncestorIndex {
  const parentOf = new Map<string, string | null>()
  const levelMap = new Map<string, number>()
  const children = new Map<string, string[]>()
  let maxLevel = 0
  for (const n of hierarchy.nodes) {
    parentOf.set(n.community_id, n.parent_id)
    levelMap.set(n.community_id, n.level)
    if (n.level > maxLevel) maxLevel = n.level
    if (n.parent_id) {
      const arr = children.get(n.parent_id) ?? []
      arr.push(n.community_id)
      children.set(n.parent_id, arr)
    }
  }
  const levelOf = (id: string) => levelMap.get(id) ?? 0
  const ancestorAt = (id: string, level: number): string => {
    let cur = id
    let guard = 0
    while (levelOf(cur) < level && parentOf.get(cur) && guard++ < 32) {
      cur = parentOf.get(cur) as string
    }
    return cur
  }
  const childrenOf = (id: string) => children.get(id) ?? []
  return { levelOf, ancestorAt, childrenOf, maxLevel }
}

// Strip the LLM boilerplate lead-in ("The theme revolves around …", "This
// community is about …") so the label surfaces the meaningful subject, not the
// repetitive prefix every summary shares. Returns null when no usable summary
// remains — caller falls back to the dominant child's crisp label.
const _SUMMARY_BOILERPLATE =
  /^(the|this)\s+(theme|community|cluster|group|topic|content|document(s)?|section)\s+((is\s+)?(revolv|center|focus|relat)(es?|ed|ing)?\s+(around|on|to)|is\s+about|concerns?|covers?|describes?|deals?\s+with)\s*/i

/** Truncate at a word boundary with an ellipsis — never mid-word (UX-15). */
function wordCap(text: string, max: number): string {
  if (text.length <= max) return text
  let cut = text.slice(0, max - 1)
  if (cut.includes(" ")) cut = cut.slice(0, cut.lastIndexOf(" "))
  return cut.replace(/[,;:]+$/, "") + "…"
}

export function cleanSummaryLabel(summary: string | null): string | null {
  if (!summary) return null
  let clause = summary.split(/[.;\n]/)[0].trim()
  clause = clause.replace(_SUMMARY_BOILERPLATE, "").trim()
  // Drop a leading article left after stripping the prefix ("the Python" → "Python").
  clause = clause.replace(/^(the|a|an)\s+/i, "").trim()
  // Drop a trailing dangling article/preposition left by the strip.
  clause = clause.replace(/\s+(the|a|an|of|for|to|in|on)$/i, "").trim()
  if (clause.length === 0) return null
  return wordCap(clause, 36)
}

/** Join the top c-TF-IDF keywords into a compact "a · b · c" label (A3). */
export function topTermsLabel(terms: string[] | null | undefined): string | null {
  if (!terms || terms.length === 0) return null
  const chip = terms.slice(0, 3).join(" · ").trim()
  return chip.length > 0 ? wordCap(chip, 36) : null
}

export function buildLevelCommunities(
  communities: CommunityHull[],
  hierarchy: CommunityHierarchy | undefined,
): CommunityHull[][] {
  if (!hierarchy || hierarchy.nodes.length === 0) return [communities]
  const ix = buildAncestorIndex(hierarchy)
  const levels: CommunityHull[][] = [communities]
  const l0ById = new Map(communities.map((c) => [c.id, c]))
  const byNode = new Map(hierarchy.nodes.map((n) => [n.community_id, n]))

  for (let L = 1; L <= ix.maxLevel; L++) {
    const out: CommunityHull[] = []
    for (const node of hierarchy.nodes) {
      if (node.level !== L) continue
      // gather descendant level-0 hulls of this level-L community
      const descendantL0: CommunityHull[] = []
      const stack = [node.community_id]
      let guard = 0
      while (stack.length && guard++ < 100000) {
        const cur = stack.pop() as string
        if (ix.levelOf(cur) === 0) {
          const h = l0ById.get(cur)
          if (h) descendantL0.push(h)
        } else {
          stack.push(...ix.childrenOf(cur))
        }
      }
      if (descendantL0.length === 0) continue
      let sx = 0, sy = 0, wsum = 0
      let dominant = descendantL0[0]
      const hullPts: [number, number][] = []
      for (const h of descendantL0) {
        const w = Math.max(1, h.count)
        sx += h.anchor[0] * w; sy += h.anchor[1] * w; wsum += w
        for (const p of h.hull) hullPts.push(p)
        if (h.count > dominant.count) dominant = h
      }
      // Label source order (A3 + UX-15): curated Community.name →
      // de-boilerplated LLM summary → c-TF-IDF top_terms chip → biggest
      // sub-community's crisp L0 label → generic id.
      const nid = node.community_id.split(":")[1] ?? node.community_id
      const label =
        node.name ??
        cleanSummaryLabel(node.summary) ??
        topTermsLabel(node.top_terms) ??
        dominant.label ??
        `Cluster ${nid}`
      out.push({
        id: node.community_id,
        count: byNode.get(node.community_id)?.member_count ?? descendantL0.reduce((s, h) => s + h.count, 0),
        anchor: [sx / wsum, sy / wsum],
        hull: hullPts,
        label,
        top_hubs: [],
        trust_mix: {},
      })
    }
    levels.push(out)
  }
  return levels
}

export function buildLevelSuperEdges(
  entities: { community: string | null }[],
  links: [number, number, number, string][],
  hierarchy: CommunityHierarchy | undefined,
): SuperEdge[][] {
  const l0 = aggregateCommunityEdges(entities as { id: string; community: string | null }[], links)
  if (!hierarchy || hierarchy.nodes.length === 0) return [l0]
  const ix = buildAncestorIndex(hierarchy)
  const out: SuperEdge[][] = [l0]
  for (let L = 1; L <= ix.maxLevel; L++) {
    const remapped = entities.map((e) => ({
      id: "",
      community: e.community ? ix.ancestorAt(e.community, L) : null,
    }))
    out.push(aggregateCommunityEdges(remapped, links))
  }
  return out
}

export function levelForRatio(
  ratio: number,
  numLevels: number,
  baseThreshold: number,
  levelStep: number,
): number {
  const raw = Math.floor((ratio - baseThreshold) / levelStep)
  return Math.max(0, Math.min(numLevels - 1, raw))
}
