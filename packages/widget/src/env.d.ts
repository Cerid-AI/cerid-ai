// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Ambient type declarations for Vite-specific import query suffixes.
 * TypeScript ignores the query string in module specifiers; these declarations
 * fill in the type so `tsc --noEmit` doesn't complain.
 */

declare module "*.css?inline" {
  const content: string;
  export default content;
}
