"use strict"

/**
 * cerid/no-error-as-empty-response
 *
 * Gate 3 (Error-is-not-empty, API layer) — 2026-08-11 consolidated audit,
 * mechanism M4: "Failure rendered as emptiness, loading, or success."
 * `fetchWatchedFolders`, `fetchIngestHistory`, `fetchOllamaStatus` (and
 * siblings across the settings/kb API modules) turn a failed HTTP response
 * into a plausible empty success, making the UI's error branch unreachable.
 * Worst instance: `useEntitlements` silently downgrades every Pro
 * entitlement to Community when `/billing/capabilities` fails, and no call
 * site reads `isError` — a paying customer watches their features vanish
 * with no error.
 *
 * This is the API-layer sibling of `cerid/no-query-error-as-empty` (which
 * covers leaf-widget `useQuery`/`useInfiniteQuery` destructuring). It flags
 * four shapes of the same defect class, all found live in this repo during
 * adversarial review of the first version of this rule:
 *
 * 1. `if (!res.ok) return <empty-shaped value>` — a fetch wrapper that
 *    collapses a non-ok HTTP response into `[]`, `{}`, or `null` instead of
 *    throwing.
 * 2. `if (res.ok) { ... } else { return <empty-shaped value> }` — the same
 *    defect spelled with the branches swapped. Semantically identical to
 *    (1); only caught by checking the success-branch shape too.
 * 3. `res.ok ? res.json() : <empty-shaped value>` (or the `!res.ok`
 *    equivalent) — the ternary form of (1)/(2), anywhere an expression is
 *    allowed, not just a `return`.
 * 4. `const { <derived props> } = useEntitlements()` (direct), OR
 *    `const result = useEntitlements(); const { <derived props> } = result`
 *    (bound to an intermediate variable first, then destructured) — either
 *    way, taking derived values without `isError`/`error` reads the hook's
 *    fallback as valid data.
 *
 * 5. `try { ...fetch()/​.ok... } catch { return <empty-shaped value> }` — a
 *    try block that performs the fetch (detected by a `fetch(...)` call or
 *    a `.ok` member access anywhere inside it) whose `catch` swallows the
 *    error and returns an empty-shaped literal instead of rethrowing.
 *    Reported as an adversarial-review fix (2026-08-11): this is the most
 *    idiomatic way to write shapes (1)-(3) and was a total blind spot in
 *    the first version of this rule, which only ever inspected `IfStatement`
 *    and `ConditionalExpression` nodes and never looked inside a
 *    `CatchClause` at all.
 *
 * Deliberately NOT flagged:
 *   - any branch (or catch clause) that throws/rethrows — the correct shape;
 *   - `if (!res.ok) return res.json()` or any non-literal return/expression
 *     — this rule only catches literal empty shapes, not general control
 *     flow;
 *   - bare `if (!res.ok) return` (void) — not "shaped like a successful
 *     response", just an aborted side effect;
 *   - non-destructured hook results (`const ent = useEntitlements()`) where
 *     `ent` is never later destructured — `ent.isError` remains reachable,
 *     mirroring the leaf-widget rule's scope decision;
 *   - a `catch` clause returning an empty literal when its `try` block has
 *     no `fetch(...)` call and no `.ok` access anywhere in it — out of
 *     scope for this rule (not identifiably an HTTP-error-as-empty catch).
 *
 * Exemptions carry their reason inline:
 *   // eslint-disable-next-line cerid/no-error-as-empty-response -- <why>
 */
module.exports = {
  meta: {
    type: "problem",
    docs: {
      description:
        "A fetch wrapper that returns an empty-shaped value on HTTP failure (via if/else, guard-return, or ternary), or a derived-data hook destructured (directly or via an intermediate binding) without isError/error, renders a backend failure indistinguishable from a legitimate empty/default state",
    },
    messages: {
      httpErrorAsEmpty:
        "This branch treats a failed HTTP response ({{subject}}.ok) as a successful empty result ({{shape}}) — the caller cannot tell a network/server failure from a real empty/default state. Throw (or propagate the error) instead, or disable with a reason if the fallback is a deliberate product decision.",
      hookErrorAsEmpty:
        "This destructuring of {{hook}}() takes {{props}} but not isError/error — a failed request will render its fallback/default value as real data.",
      catchErrorAsEmpty:
        "This catch block swallows an HTTP error (its try block calls fetch()/checks .ok) and returns a successful empty result ({{shape}}) — rethrow, or disable with a reason if the fallback is a deliberate product decision.",
    },
    schema: [],
  },
  create(context) {
    const DERIVED_DATA_HOOKS = new Set(["useEntitlements"])

    // Matches `!x.ok` — returns the subject (`x`) when the test is a
    // negated `.ok` access, i.e. the branch it guards runs on failure.
    function notOkSubject(test) {
      if (
        test.type === "UnaryExpression" &&
        test.operator === "!" &&
        test.argument.type === "MemberExpression" &&
        !test.argument.computed &&
        test.argument.property.type === "Identifier" &&
        test.argument.property.name === "ok"
      ) {
        return test.argument.object
      }
      return null
    }

    // Matches bare `x.ok` — returns the subject when the test is a plain
    // `.ok` access, i.e. the branch it guards runs on success (so the
    // *other* branch is the failure path).
    function okSubject(test) {
      if (
        test.type === "MemberExpression" &&
        !test.computed &&
        test.property.type === "Identifier" &&
        test.property.name === "ok"
      ) {
        return test.object
      }
      return null
    }

    function emptyShapeLabel(arg) {
      if (!arg) return null
      if (arg.type === "ObjectExpression") return "an object literal"
      if (arg.type === "ArrayExpression") return "an array literal"
      if (arg.type === "Literal" && arg.value === null) return "null"
      return null
    }

    // Given a branch node (the consequent or alternate of an IfStatement),
    // find the ReturnStatement that would fire — but only if that branch
    // doesn't throw. A branch that throws is the correct shape regardless
    // of what else it contains.
    function findReturnInBranch(branch) {
      if (!branch) return null
      if (branch.type === "ReturnStatement") return branch
      if (branch.type === "BlockStatement") {
        const hasThrow = branch.body.some((s) => s.type === "ThrowStatement")
        if (hasThrow) return null
        return branch.body.find((s) => s.type === "ReturnStatement") || null
      }
      return null
    }

    function reportEmptyReturn(subject, returnStmt) {
      if (!returnStmt) return
      const shape = emptyShapeLabel(returnStmt.argument)
      if (!shape) return
      context.report({
        node: returnStmt,
        messageId: "httpErrorAsEmpty",
        data: {
          subject: subject.type === "Identifier" ? subject.name : "response",
          shape,
        },
      })
    }

    function reportEmptyExpression(subject, expr) {
      const shape = emptyShapeLabel(expr)
      if (!shape) return
      context.report({
        node: expr,
        messageId: "httpErrorAsEmpty",
        data: {
          subject: subject.type === "Identifier" ? subject.name : "response",
          shape,
        },
      })
    }

    function checkHookDestructure(objectPattern, hookName) {
      let takesDerived = false
      let takesErrorSignal = false
      const propNames = []
      for (const prop of objectPattern.properties) {
        if (prop.type !== "Property" || prop.key.type !== "Identifier") continue
        propNames.push(prop.key.name)
        if (prop.key.name === "isError" || prop.key.name === "error") {
          takesErrorSignal = true
        } else if (prop.key.name !== "isLoading") {
          takesDerived = true
        }
      }
      if (takesDerived && !takesErrorSignal) {
        context.report({
          node: objectPattern,
          messageId: "hookErrorAsEmpty",
          data: { hook: hookName, props: propNames.join(", ") },
        })
      }
    }

    // Recursively searches a subtree for `fetch(...)` or `.ok` — the
    // signal that a try block is handling an HTTP response, so its catch
    // swallowing the error into an empty literal is this rule's defect
    // class rather than an unrelated try/catch (e.g. JSON.parse).
    function containsFetchSignature(node, seen) {
      if (!node || typeof node !== "object" || typeof node.type !== "string") return false
      if (seen.has(node)) return false
      seen.add(node)
      if (
        node.type === "CallExpression" &&
        node.callee.type === "Identifier" &&
        node.callee.name === "fetch"
      ) {
        return true
      }
      if (
        node.type === "MemberExpression" &&
        !node.computed &&
        node.property.type === "Identifier" &&
        node.property.name === "ok"
      ) {
        return true
      }
      for (const key of Object.keys(node)) {
        if (key === "parent" || key === "loc" || key === "range") continue
        const value = node[key]
        if (Array.isArray(value)) {
          for (const item of value) {
            if (item && typeof item.type === "string" && containsFetchSignature(item, seen)) {
              return true
            }
          }
        } else if (value && typeof value.type === "string") {
          if (containsFetchSignature(value, seen)) return true
        }
      }
      return false
    }

    // Tracks `const result = useEntitlements()` bindings (plain identifier,
    // not destructured yet) so a later `const { ... } = result` is still
    // caught — destructuring doesn't have to happen in the same statement
    // as the call.
    const hookResultBindings = new Map()

    return {
      TryStatement(node) {
        if (!node.handler || !node.handler.body) return
        if (!containsFetchSignature(node.block, new Set())) return
        const returnStmt = findReturnInBranch(node.handler.body)
        if (!returnStmt) return
        const shape = emptyShapeLabel(returnStmt.argument)
        if (!shape) return
        context.report({
          node: returnStmt,
          messageId: "catchErrorAsEmpty",
          data: { shape },
        })
      },
      IfStatement(node) {
        const notOk = notOkSubject(node.test)
        if (notOk) {
          reportEmptyReturn(notOk, findReturnInBranch(node.consequent))
          return
        }
        const ok = okSubject(node.test)
        if (ok && node.alternate) {
          reportEmptyReturn(ok, findReturnInBranch(node.alternate))
        }
      },
      ConditionalExpression(node) {
        const notOk = notOkSubject(node.test)
        if (notOk) {
          reportEmptyExpression(notOk, node.consequent)
          return
        }
        const ok = okSubject(node.test)
        if (ok) {
          reportEmptyExpression(ok, node.alternate)
        }
      },
      VariableDeclarator(node) {
        if (!node.init) return

        // Direct: const { x } = useEntitlements() / const result = useEntitlements()
        if (
          node.init.type === "CallExpression" &&
          node.init.callee.type === "Identifier" &&
          DERIVED_DATA_HOOKS.has(node.init.callee.name)
        ) {
          if (node.id.type === "ObjectPattern") {
            checkHookDestructure(node.id, node.init.callee.name)
          } else if (node.id.type === "Identifier") {
            hookResultBindings.set(node.id.name, node.init.callee.name)
          }
          return
        }

        // Indirect: const result = useEntitlements(); const { x } = result
        if (
          node.id.type === "ObjectPattern" &&
          node.init.type === "Identifier" &&
          hookResultBindings.has(node.init.name)
        ) {
          checkHookDestructure(node.id, hookResultBindings.get(node.init.name))
        }
      },
    }
  },
}
