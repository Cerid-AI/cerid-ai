# UX Copy — Verification Surfaces

> Canonical strings for verification-adjacent UI surfaces.
> All strings MUST be imported from `src/web/src/lib/ux-copy.ts`.
> Do NOT inline verification copy in component files.

## Verification bands

| State | Display string |
|---|---|
| Verified (n sources) | "Verified by {n} source(s)" |
| Partial source | "Partial source" |
| No source | "No source found for this claim" |

## Badge labels (compact form)

| State | Badge text |
|---|---|
| verified | "Verified by {n} source(s)" |
| partial | "Partial source" |
| unverified | "No source" |

## Streaming / loading

| Context | String |
|---|---|
| While verifying | "Verifying…" |

## Error states

| Error | String |
|---|---|
| Source unreachable | "Source unreachable — try again" |

## Hover/popover (progressive disclosure)

| Field | String |
|---|---|
| Confidence score | "Confidence: {n}" |
| View source link | "View source" |

## Accessibility (aria-labels)

| State | aria-label |
|---|---|
| Verified badge | "Claim verified by {n} source(s)" |
| Partial badge | "Claim has partial source" |
| Unverified badge | "Claim has no source" |

## Usage

```typescript
import { UX_COPY } from "@/lib/ux-copy"

// Examples
UX_COPY.verification.verified(2)           // "Verified by 2 sources"
UX_COPY.verification.partial               // "Partial source"
UX_COPY.verification.noSource              // "No source"
UX_COPY.verification.verifying             // "Verifying…"
UX_COPY.verification.sourceUnreachable     // "Source unreachable — try again"
UX_COPY.verification.confidence(0.93)      // "Confidence: 0.93"
UX_COPY.verification.viewSource            // "View source"
UX_COPY.verification.ariaVerified(2)       // "Claim verified by 2 sources"
UX_COPY.verification.ariaPartial           // "Claim has partial source"
UX_COPY.verification.ariaUnverified        // "Claim has no source"
```
