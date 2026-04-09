/**
 * Internal-only type extensions for trading, billing, and enterprise features.
 *
 * This file exists only in cerid-ai-internal. The public repo uses
 * the base types.ts without these extensions.
 */

// Re-export everything from the base types
export * from './types'

// Override PluginStatus with enterprise variants
export type PluginStatus = "installed" | "active" | "error" | "disabled" | "requires_pro" | "requires_enterprise"

// Enterprise tier type
export type FeatureTier = "community" | "pro" | "enterprise"

// Extended settings interface with trading support
export interface InternalCeridSettings {
  trading_enabled?: boolean
}
