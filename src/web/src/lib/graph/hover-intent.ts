// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Shared hover-intent delay used by all graph surfaces (Atlas + Cartographer).
// One value, one place — changing it here propagates to both views.

/**
 * How long the pointer must dwell over a node before the hover card appears.
 * 300ms is long enough to filter accidental hover-throughs, short enough
 * that the card feels responsive.
 */
export const HOVER_INTENT_DELAY_MS = 300
