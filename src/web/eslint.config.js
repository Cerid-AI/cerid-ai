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

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
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
])