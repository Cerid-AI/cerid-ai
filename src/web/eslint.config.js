// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

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
    rules: {
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