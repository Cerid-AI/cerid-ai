"use strict"

/**
 * cerid/no-query-error-as-empty
 *
 * A useQuery consumer that destructures `data` but neither `isError` nor
 * `error` cannot distinguish "the fetch failed" from "the list is empty" —
 * the failure renders as an empty state (FE-06 / P7, 2026-08-05 GA audit).
 * ActivityFeed polled a failing backend forever while showing "No ingestion
 * activity yet."; graph-preview showed "No graph connections found" on a 500.
 *
 * Flags: `const { data, isLoading } = useQuery(...)` — any ObjectPattern over
 * a useQuery/useInfiniteQuery call that takes `data` (or `data: alias`)
 * without also taking `isError` or `error`.
 *
 * Deliberately NOT flagged:
 *   - non-destructured results (`const q = useQuery(...)`) — access to
 *     `q.isError` can't be cheaply proven absent, and the common failure
 *     shape in this codebase is the destructuring one;
 *   - patterns that don't take `data` at all (mutation-style side uses).
 *
 * Ambient widgets where silence is a product decision (health polls, credit
 * badges) opt out with an inline
 *   // eslint-disable-next-line cerid/no-query-error-as-empty -- <why>
 * so every exemption carries its reason in place.
 *
 * Warn-only at introduction per repo convention (flip after four green runs).
 * Note: this rule cannot see hand-rolled fetch loops that swallow into state
 * (ActivityFeed's original shape) — that class is caught by review + the
 * DataState adoption, not by this AST check.
 */
module.exports = {
  meta: {
    type: "problem",
    docs: {
      description:
        "useQuery destructuring that takes data but ignores isError/error renders failures as empty states",
    },
    messages: {
      errorAsEmpty:
        "This useQuery destructuring takes `data` but neither `isError` nor `error` — a failed fetch will render as an empty state. Destructure `isError` (and show an error/degraded state), or disable with a reason if silence is intentional.",
    },
    schema: [],
  },
  create(context) {
    const QUERY_HOOKS = new Set(["useQuery", "useInfiniteQuery"])
    return {
      VariableDeclarator(node) {
        if (
          !node.init ||
          node.init.type !== "CallExpression" ||
          node.init.callee.type !== "Identifier" ||
          !QUERY_HOOKS.has(node.init.callee.name) ||
          node.id.type !== "ObjectPattern"
        ) {
          return
        }
        let takesData = false
        let takesErrorSignal = false
        for (const prop of node.id.properties) {
          if (prop.type !== "Property" || prop.key.type !== "Identifier") continue
          if (prop.key.name === "data") takesData = true
          if (prop.key.name === "isError" || prop.key.name === "error") {
            takesErrorSignal = true
          }
        }
        if (takesData && !takesErrorSignal) {
          context.report({ node: node.id, messageId: "errorAsEmpty" })
        }
      },
    }
  },
}
