// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'
import { createRequire } from 'module'

const require = createRequire(import.meta.url)
const noUnsafeArrayOnQueryData = require('./eslint-rules/no-unsafe-array-on-query-data.cjs')
const noQueryErrorAsEmpty = require('./eslint-rules/no-query-error-as-empty.cjs')
const noErrorAsEmptyResponse = require('./eslint-rules/no-error-as-empty-response.cjs')

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx,mts,cts}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      jsxA11y.flatConfigs.recommended,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      cerid: {
        rules: {
          'no-unsafe-array-on-query-data': noUnsafeArrayOnQueryData,
          'no-query-error-as-empty': noQueryErrorAsEmpty,
          'no-error-as-empty-response': noErrorAsEmptyResponse,
        },
      },
    },
    rules: {
      'cerid/no-unsafe-array-on-query-data': 'warn',
      // FE-06/P7 (2026-08-05 GA audit): a useQuery destructuring that takes
      // `data` but ignores isError/error renders failures as empty states.
      // Warn-only at introduction per repo convention; ambient widgets where
      // silence is deliberate opt out inline with a reason.
      'cerid/no-query-error-as-empty': 'warn',
      // Gate 3 (2026-08-11 consolidated audit, mechanism M4): the API-layer
      // sibling of the rule above. A fetch wrapper that returns `[]`/`{}`/
      // `null` on a failed HTTP response, or a `useEntitlements()`
      // destructuring that takes derived data without `isError`/`error`,
      // renders a backend failure indistinguishable from a legitimate
      // empty/default state. Blocking (not warn-only) — this is a gate, not
      // an introduction sweep. Every current violation is grandfathered with
      // an inline eslint-disable-next-line citing the audit; a NEW site must
      // throw/propagate the error or disable with its own reason.
      'cerid/no-error-as-empty-response': 'error',
      // EC1 guard: never fetch() a bare API path. A relative MCP_BASE makes
      // `new URL(path)` throw, and a missing /api/mcp prefix falls through nginx
      // to the SPA shell (HTTP 200 text/html) and explodes on `.json()`. Route
      // every MCP request through mcpUrl()+mcpHeaders() (lib/api/common.ts).
      // The selectors only match templates/strings that START with a literal
      // "/", so `fetch(`${MCP_BASE}/x`)` and `mcpUrl(...)` are allowed. For a
      // genuine static-asset fetch, add an eslint-disable-next-line with a reason.
      'no-restricted-syntax': [
        'error',
        {
          selector: "CallExpression[callee.name='fetch'] > Literal[value=/^\\//]",
          message: 'Do not fetch() a bare API path — use mcpUrl()+mcpHeaders() from @/lib/api/common (EC1: nginx returns the SPA shell for an unprefixed path).',
        },
        {
          selector: "CallExpression[callee.name='fetch'] > TemplateLiteral > TemplateElement:first-child[value.raw=/^\\//]",
          message: 'Do not fetch() a bare API path — use mcpUrl()+mcpHeaders() from @/lib/api/common (EC1: nginx returns the SPA shell for an unprefixed path).',
        },
      ],
      // react-hooks v7 strict rules — warn for now, fix incrementally.
      // 7.1.x added `refs`, `immutability`, `component-hook-factories`,
      // and `preserve-manual-memoization` as errors-by-default; existing
      // codebase patterns trip them (`use-verification-orchestrator.ts`
      // accesses .current during render, plugins-section.tsx defines a
      // component inside render). Demote to warn while migrating off
      // these patterns in a dedicated cleanup sprint.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/purity': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/immutability': 'warn',
      'react-hooks/component-hook-factories': 'warn',
      'react-hooks/static-components': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      // shadcn/ui files export variant helpers alongside components
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // jsx-a11y — UX audit baseline. All recommended rules surface as
      // warnings so the audit triage can route them; CI stays green.
      // High-frequency rules are explicitly named here for visibility.
      'jsx-a11y/no-autofocus': 'warn',
      'jsx-a11y/label-has-associated-control': 'warn',
      'jsx-a11y/click-events-have-key-events': 'warn',
      'jsx-a11y/no-static-element-interactions': 'warn',
      'jsx-a11y/no-noninteractive-element-interactions': 'warn',
      'jsx-a11y/anchor-is-valid': 'warn',
    },
  },
  {
    // Gate 3 coverage gap (2026-08-11 adversarial review): the block above
    // now covers `**/*.{ts,tsx,mts,cts}`, but a violation written in plain
    // .js/.jsx would still parse and lint under zero rules without this
    // block. src/web/src is all-TypeScript today, but this closes the gap
    // rather than leaving it implicit. Deliberately minimal: just the one
    // rule, not the full TS-oriented `extends` chain above (which assumes
    // a TS parser project).
    //
    // .mjs/.cjs added (2026-08-11 error-not-empty audit): the glob above
    // never matched .mjs/.cjs either, so a fetch wrapper written as e.g.
    // `src/lib/api/x.mjs` carried the exact error-as-empty defect this
    // rule exists to catch, invisibly. The new extension pair is scoped to
    // `src/**` (app source) rather than repo-root so it doesn't reach root
    // tooling files like `eslint-rules/*.cjs` (CommonJS rule modules, not
    // app source) or root config files; `**/*.{js,jsx}` is left as-is so
    // existing coverage (e.g. `public/sw.js`) doesn't shrink.
    files: ['**/*.{js,jsx}', 'src/**/*.{mjs,cjs}'],
    plugins: {
      cerid: {
        rules: {
          'no-error-as-empty-response': noErrorAsEmptyResponse,
        },
      },
    },
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'module',
      globals: globals.browser,
    },
    rules: {
      'cerid/no-error-as-empty-response': 'error',
    },
  },
])