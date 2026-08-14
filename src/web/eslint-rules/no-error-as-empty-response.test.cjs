"use strict"

// Minimal RuleTester coverage for cerid/no-error-as-empty-response (Gate 3).
// Neither sibling rule in this directory had a test before this one; run
// directly with `node eslint-rules/no-error-as-empty-response.test.cjs`
// (RuleTester falls back to its own assert-based runner when no test
// framework's global describe/it is present).
//
// The `if (res.ok) {...} else {return <empty>}`, ternary, and
// intermediate-binding-hook cases below were added after an adversarial
// review found the first version of this rule missed all three; the
// try/catch cases were added after a second adversarial pass found the
// rule had zero coverage for `try { fetch()... } catch { return <empty> }`
// — see the module docblock in no-error-as-empty-response.cjs.
//
// IMPORTANT: this file is a closed RuleTester fixture set. It proves the
// rule's logic against the snippets below; it does NOT scan any file in
// src/web/src and is not itself a gate over the codebase. The actual gate
// is `cerid/no-error-as-empty-response: 'error'` in eslint.config.js,
// enforced wherever ESLint already runs over the tree — `make lint-frontend`,
// `make ci-local` (part of `make prepush`), and `npm run lint` inside CI's
// `frontend` job (scripts/ci/frontend.sh). No separate wiring for this file
// is required or attempted; run it directly only as a fast unit check on
// the rule itself: `node eslint-rules/no-error-as-empty-response.test.cjs`.

const { RuleTester } = require("eslint")
const rule = require("./no-error-as-empty-response.cjs")

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: "module" },
})

ruleTester.run("no-error-as-empty-response", rule, {
  valid: [
    // Throwing on a failed response is the correct shape.
    `async function f() {
      const res = await fetch("/x")
      if (!res.ok) throw new Error("failed")
      return res.json()
    }`,
    // Returning the parsed body (not a literal) isn't a masked failure.
    `async function f() {
      const res = await fetch("/x")
      if (!res.ok) return res.json()
      return res.json()
    }`,
    // A bare void return isn't "shaped like a successful response".
    `async function f() {
      const res = await fetch("/x")
      if (!res.ok) return
      const data = await res.json()
    }`,
    // if/else form: success branch returns the parsed body, failure
    // branch throws — the correct shape spelled with branches swapped.
    `async function f() {
      const res = await fetch("/x")
      if (res.ok) {
        return res.json()
      } else {
        throw new Error("failed")
      }
    }`,
    // if/else form: failure branch returns a non-literal — not a masked
    // empty shape.
    `async function f() {
      const res = await fetch("/x")
      if (res.ok) {
        return res.json()
      } else {
        return res.json()
      }
    }`,
    // Ternary form: failure branch is a thrown IIFE / non-literal
    // expression, not an empty-shaped literal.
    `async function f() {
      const res = await fetch("/x")
      return res.ok ? res.json() : res.json()
    }`,
    // Destructuring isError alongside derived hook data is fine.
    `function useThing() {
      const { tier, isError } = useEntitlements()
      return tier
    }`,
    // Destructuring error alongside derived hook data is fine.
    `function useThing() {
      const { forDef, error } = useEntitlements()
      return forDef
    }`,
    // Only isLoading, no other derived data taken.
    `function useThing() {
      const { isLoading } = useEntitlements()
      return isLoading
    }`,
    // Non-destructured hook result — isError remains reachable.
    `function useThing() {
      const ent = useEntitlements()
      return ent.tier
    }`,
    // Bound to an intermediate variable, then only member-accessed
    // (never destructured) — isError remains reachable via `result.isError`.
    `function useThing() {
      const result = useEntitlements()
      return result.tier
    }`,
    // A hook not in the derived-data set is out of scope for this rule.
    `function useThing() {
      const { data } = useSomethingElse()
      return data
    }`,
    // Bypass 4 fix: catch clause rethrows — the correct shape.
    `async function fetchThings() {
      try {
        const res = await fetch("/things")
        if (!res.ok) throw new Error("failed")
        return res.json()
      } catch (err) {
        throw err
      }
    }`,
    // catch returns a non-literal (e.g. a caller-supplied fallback function
    // result) — not a masked empty shape.
    `async function fetchThings(fallback) {
      try {
        const res = await fetch("/things")
        if (!res.ok) throw new Error("failed")
        return res.json()
      } catch (err) {
        return fallback()
      }
    }`,
    // try block has no fetch()/.ok signature — out of scope for this rule
    // (e.g. a JSON.parse retry loop, unrelated to HTTP).
    `function parseThing(raw) {
      try {
        return JSON.parse(raw)
      } catch {
        return {}
      }
    }`,
  ],
  invalid: [
    {
      code: `async function fetchWatchedFolders() {
        const res = await fetch("/watched-folders")
        if (!res.ok) return { folders: [], total: 0 }
        return res.json()
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    {
      code: `async function fetchIngestHistory() {
        const res = await fetch("/admin/ingest-history")
        if (!res.ok) return { items: [], total: 0, next_cursor: null }
        return res.json()
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    {
      code: `async function fetchVaultProfile() {
        const res = await fetch("/vault-profile")
        if (!res.ok) return null
        return res.json()
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    {
      code: `async function fetchThings() {
        const res = await fetch("/things")
        if (!res.ok) return []
        return res.json()
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    // Bypass 1: if/else with the branches swapped — semantically
    // identical to the `if (!res.ok) return <empty>` shape above.
    {
      code: `async function fetchOllamaStatus() {
        const res = await fetch("/ollama/status")
        if (res.ok) {
          return res.json()
        } else {
          return { status: "unknown", models: [] }
        }
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    {
      code: `async function fetchThings() {
        const res = await fetch("/things")
        if (res.ok) {
          return res.json()
        } else {
          return []
        }
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    // Bypass 2: ternary form, both polarities.
    {
      code: `async function fetchThings() {
        const res = await fetch("/things")
        return res.ok ? res.json() : []
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    {
      code: `async function fetchThings() {
        const res = await fetch("/things")
        return !res.ok ? {} : res.json()
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    {
      code: `async function fetchVaultProfile() {
        const res = await fetch("/vault-profile")
        return res.ok ? res.json() : null
      }`,
      errors: [{ messageId: "httpErrorAsEmpty" }],
    },
    {
      code: `function useThing() {
        const { tier } = useEntitlements()
        return tier
      }`,
      errors: [{ messageId: "hookErrorAsEmpty" }],
    },
    {
      code: `function useThing() {
        const { forFlag, isLoading } = useEntitlements()
        return forFlag
      }`,
      errors: [{ messageId: "hookErrorAsEmpty" }],
    },
    // Bypass 3: hook result bound to an intermediate variable before
    // destructuring.
    {
      code: `function useThing() {
        const result = useEntitlements()
        const { tier, licenseState } = result
        return tier
      }`,
      errors: [{ messageId: "hookErrorAsEmpty" }],
    },
    {
      code: `function useThing() {
        const result = useEntitlements()
        const { forFlag } = result
        return forFlag
      }`,
      errors: [{ messageId: "hookErrorAsEmpty" }],
    },
    // Bypass 4: try/catch swallowing an HTTP error into an empty literal —
    // the adversarial reviewer's exact proof-of-concept, previously a total
    // blind spot (0 errors from `npx eslint`).
    {
      code: `async function fetchThings() {
        try {
          const res = await fetch("/things")
          if (!res.ok) throw new Error("failed")
          return res.json()
        } catch {
          return []
        }
      }`,
      errors: [{ messageId: "catchErrorAsEmpty" }],
    },
    {
      code: `async function fetchOllamaStatus() {
        try {
          const res = await fetch("/ollama/status")
          if (!res.ok) throw new Error("failed")
          return res.json()
        } catch (err) {
          return { status: "unknown", models: [] }
        }
      }`,
      errors: [{ messageId: "catchErrorAsEmpty" }],
    },
    {
      code: `async function fetchVaultProfile() {
        try {
          const res = await fetch("/vault-profile")
          if (!res.ok) throw new Error("failed")
          return res.json()
        } catch {
          return null
        }
      }`,
      errors: [{ messageId: "catchErrorAsEmpty" }],
    },
    // Same shape, but the fetch call is wrapped through a helper that only
    // checks `.ok` (no literal `fetch(` identifier in the try block) —
    // confirms the `.ok`-access signal alone is sufficient.
    {
      code: `async function fetchThings() {
        try {
          const res = await getResponse("/things")
          if (!res.ok) throw new Error("failed")
          return res.json()
        } catch {
          return []
        }
      }`,
      errors: [{ messageId: "catchErrorAsEmpty" }],
    },
  ],
})

console.log("no-error-as-empty-response: all RuleTester cases passed")
