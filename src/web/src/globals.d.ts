// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Version baked at build time from `src/web/package.json` — see the `define`
 * block in `vite.config.ts`.
 *
 * It exists because `VITE_APP_VERSION` is threaded through docker-compose as
 * `${VITE_APP_VERSION:-}` and nothing ever set it, so every production build
 * reported its Sentry release as "dev". The runtime env still wins when set;
 * this is the honest default underneath it.
 */
declare const __APP_VERSION__: string
