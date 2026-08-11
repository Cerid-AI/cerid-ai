# Licensing, trials, and activation

How to try Cerid Pro, how to buy it, and how entitlement is decided at runtime.
For the flag-by-flag feature split see [`TIER_MATRIX.md`](TIER_MATRIX.md); for
the source licenses that govern each directory see
[`../CONTRIBUTING.md`](../CONTRIBUTING.md#license).

## Tiers

| Tier | Price | What it is |
|------|-------|-----------|
| **Core** | Free | The whole self-hosted product: all four knowledge surfaces, 12 agents, 55 MCP tools, local + web sources, the local LLM pipeline, and both SDKs. No account, no telemetry, no seat limit, no expiry. |
| **Pro** | $15/mo · $144/yr | Adds everything that reaches outside your own disk — cloud and Apple connectors, Meeting Capture, custom Smart RAG, advanced analytics, daily digest, inbox triage, metamorphic verification. |
| **Vault** | Contact | Enterprise: multi-user with tenant isolation, SSO/SAML, audit logging, SLA and deployment support. <vault@cerid.ai> |

## Try Pro free for 14 days

**Settings → Plan & Billing → Start 14-day free trial.** No credit card, no
account, no data leaves the machine — the server grants the trial to itself and
it expires locally. One trial per installation.

When it ends the server drops back to Core. Nothing is deleted: sources you
connected during the trial stop syncing, and the knowledge they already
produced stays in your knowledge base.

## Buy and activate

1. Pick a plan at [cerid.ai/pricing](https://cerid.ai/pricing).
2. Checkout is hosted on cerid.ai. A self-hosted Cerid server never contacts a
   payment provider and never sees card data.
3. You get an Ed25519-signed license key (also shown on the post-purchase page
   and in your receipt email).
4. **Settings → Plan & Billing**, paste the key, choose **Activate**.

Validation is offline: the key carries its own tier and expiry, so activation
works on an air-gapped host and no license server is ever contacted. Upgrading
needs no reinstall and no restart.

## How keys are verified

Keys are **Ed25519-signed and offline-verifiable**. Cerid signs each key with a
private key held only on the billing host; your server verifies it with the
**public** key compiled into the app. A public key can verify signatures but
cannot produce them, so shipping it in source-available code gives away nothing
— keys cannot be minted from this repository.

A key is `CERID-PRO-` followed by base32 groups of four encoding 72 bytes: an
8-byte signed payload (`expiry_day`, `tier_byte`, scheme `version`, and a 4-byte
email fingerprint kept for audit only) plus the 64-byte signature over it.
Because the expiry and tier live *inside* the signed payload, neither can be
edited without invalidating the signature.

Verification is entirely local — no license server, no network call, works
air-gapped. A key is rejected if the signature does not match, if it was signed
by anyone else, if any byte of it has been altered, if the scheme version is
retired, or if it has expired.

> One quirk worth knowing: the final character of a key encodes a single
> significant bit, so a typo there can still decode to the same key. Every byte
> of the payload and signature is covered; only that one trailing character is
> slack.

Setting `CERID_LICENSE_PUBLIC_KEY` to an empty string disables verification and
accepts any correctly-shaped key. That exists for local preview only, and the
server logs a warning on every boot while it is in effect.

## How the runtime tier is decided

Three sources compose, and the **most capable one wins** — a source can raise
the tier, never lower it:

| Source | Set by | Notes |
|--------|--------|-------|
| `CERID_TIER` env var | Operator | The **baseline floor**. Deactivating a license or ending a trial falls back to this, not to `community`. |
| Active trial | Settings → Plan & Billing | Grants Pro until its expiry. |
| Activated license key | Settings → Plan & Billing | Grants the key's tier until the key's embedded expiry. |

So an operator who pins `CERID_TIER=enterprise` on an air-gapped box keeps
Enterprise even while a Pro trial is running, and a customer whose key lapses
returns to whatever the operator configured rather than being knocked to Core.

Entitlement is re-derived at every startup, so an activated license survives a
container restart. If it did not, `FEATURE_TIER` would fall back to the env
value on each boot and paid features would silently disappear.

### `CERID_TIER` for operators

The variable exists for deployments where interactive activation is impractical
— air-gapped installs, enterprise images, CI. It is not the intended purchase
path, and setting it does not grant a license to the plugin trees, whose own
terms are in [`../plugins/LICENSE`](../plugins/LICENSE) (BUSL-1.1).

Using it to enable paid features without a license puts the server in the
**unlicensed-Pro** state. Nothing is blocked, degraded, or time-limited — but
the server stops pretending it is licensed:

- a non-dismissible notice on the Pro settings panes, and an **Unlicensed**
  marker in the status bar. The status bar renders on every screen, but it
  is hidden below the `md` breakpoint — on a narrow window the mobile tab
  bar occupies that position — so on a phone the settings-pane notice is
  the only marker (this doc said "on every screen" until 2026-08-10);
- a `license_notice` WARNING in the server log on every boot;
- `"Generated by an unlicensed copy of Cerid Pro"` stamped into Pro-generated
  artifacts such as the daily digest.

All of it clears the moment you activate a key or start the free trial. A
licensed customer who *also* pins `CERID_TIER` is reported as licensed — the
key wins — and a server mid-trial is reported as trialing, not unlicensed.

Cerid is source-available, so a determined reader can always change what runs
on their own machine. There is no phone-home, no seat enforcement, and no
attempt at DRM. Pro exists because the connectors and the hosted issuance
behind them cost real money to build and keep working; paying for it is what
keeps Core free and maintained.

## Endpoints

The community server exposes these locally (no network egress):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/license/status` | Current tier, its source, and trial state |
| `GET` | `/license/capabilities` | Per-feature entitlement map + `license_state` (drives the lock badges and the unlicensed notice) |
| `POST` | `/license/activate` | Validate and persist a key |
| `POST` | `/license/deactivate` | Drop back to the baseline tier |
| `POST` | `/license/trial` | Start the one-time 14-day trial |

## Troubleshooting

**"Invalid or expired license key."** The signature did not verify. Usually a
truncated paste — copy the whole key including the `CERID-PRO-` prefix and every
dash group. Otherwise the key has expired, or it was not issued by Cerid.

**"Invalid license key format."** The text is not shaped like a key at all
(wrong prefix, or it does not decode to 72 bytes).

**Pro features still locked after activating.** Check
`GET /license/status` reports `tier: "pro"`. If it does and the UI disagrees,
the browser is holding a cached capabilities response for up to 60 seconds.

**A feature is Pro-marked but still off on Pro.** That is a server flag, not a
plan limit — the feature's env var is disabled. Settings names the variable
inline for exactly this case.

**Moving to a new machine.** Activate the same key there. Cerid records which
host last activated a key for audit only; it is not enforced.
