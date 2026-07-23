// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Honesty-first RAG system preamble injected before retrieved document blocks.
 *
 * Rules in force:
 *  (1) Ground answers in documents and cite specifics when they answer the question.
 *  (2) Clearly distinguish document-facts from general knowledge.
 *  (3) Qualify time-sensitive values with the document's date when shown; otherwise
 *      treat them as recorded (possibly outdated) values and suggest a live source
 *      when currency matters.
 *  (4) When documents don't cover the question, say so plainly, then answer from
 *      general knowledge if possible — labeled as such.
 *  (5) For analytical questions (counting/combining across documents, date
 *      arithmetic, applying stated preferences), reason step by step and derive
 *      the answer rather than refusing when the underlying facts are present.
 *
 * Note: document blocks carry type= and date= attributes (Phase 1.2 landed).
 * Rule (3) references "the document's date if shown" — the date= attribute supplies
 * that value when the backend includes created_at on the source object.
 */
/**
 * Stable machine marker stamped at the head of every KB-injected system message.
 * The server's Private Mode L2 backstop (private_mode.py::_INJECTION_MARKERS)
 * matches on THIS token, not the human-readable preamble copy below — which was
 * previously string-coupled, so a reword silently broke the backstop and leaked
 * the KB to the model at L2+ (E1 CR-080). Keep byte-identical to the Python marker.
 */
export const KB_CONTEXT_SENTINEL = "<!--cerid:kb-context-->"

export const RAG_SYSTEM_PREAMBLE =
  `${KB_CONTEXT_SENTINEL}\n` +
  `The user has a personal knowledge base. Below are documents retrieved for this conversation; each is tagged with its source. Rules: (1) When the documents answer the question, ground your answer in them and cite specifics. (2) Distinguish clearly between facts from these documents and your general knowledge. (3) For time-sensitive values (prices, versions, schedules), treat the documents as point-in-time records — qualify with the document's date if shown, otherwise present the value as a recorded (possibly outdated) value, never as current; suggest checking a live source when currency matters. When documents conflict about the same fact, trust the most recently dated one. (4) If the documents don't cover the question, say so plainly, then answer from general knowledge if you can, labeled as such. A clear "your knowledge base doesn't cover this" is better than a guess. (5) For analytical questions — counting or combining facts across documents, date arithmetic (how long between events, which came first), or applying the user's stated preferences — reason step by step across the documents and DERIVE the answer; don't refuse just because no single document states it outright. Only say you can't answer when the underlying facts are genuinely absent.`
