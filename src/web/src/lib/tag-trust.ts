// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Whether artifact tags are trustworthy enough to show as fact.
 *
 * Artifact tags are LLM-extracted at ingest (`ai_categorize`) and merged with
 * any document frontmatter. Judged blind against document content on
 * 2026-08-14, only ~34% of tag instances accurately describe their artifact,
 * against a 7.5-12% decoy floor — the judge discriminates, so the number is
 * about the tags. Document-type labels are the worst: `invoice` 0/11,
 * `receipt` 0/8, `tutorial` 0/8, `tax-return` 0/4. Observed instances include a
 * gun-rights fundraising appeal and a hotel-rewards promotion both tagged
 * `invoice`.
 *
 * A chip that is wrong two times in three asserts something false at a glance,
 * and a filter built on it returns the wrong artifacts. Both are suppressed
 * until the extractor is fixed.
 *
 * Ingest still WRITES tags and the Tag Manager still reads them, so nothing is
 * lost and no backfill is required — this hides the surfaces that present a tag
 * as a settled fact about an artifact.
 *
 * To re-enable: fix the extractor, re-run `src/mcp/tests/eval/tag_index_eval.py`
 * (in-container; the stage-routing vars are in its docstring), and flip this to
 * `true` when real-tag acceptance clears the decoy floor by a wide margin.
 *
 * NOTE: there is no stored provenance separating LLM-extracted tags from
 * frontmatter or user-entered ones, so this suppresses all three. Adding that
 * provenance is the proper fix and is tracked with the extractor work.
 */
export const TAGS_TRUSTED = false
