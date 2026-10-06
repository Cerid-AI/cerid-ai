# Pro Connector — Outlook

## What this connector does

The Outlook Pro connector lets chat answers cite messages from your
Microsoft 365 / Outlook mailbox alongside local KB artifacts. When a
question would benefit from recent email context, the connector searches
Outlook on demand, pulls the most-relevant messages, and hands them to
the retrieval pipeline as first-class evidence with proper provenance
(sender, subject, timestamp, message-id).

Nothing is bulk-indexed for v1 — search-on-demand only. The mailbox stays
where it is; only the matching results enter the answer context window.

## Architecture

The connector runs as a **sibling MCP server** in its own Docker container,
not inside the Cerid backend:

- Image: built from source in `stacks/connectors/docker-compose.yml`
  (`build.context` = the upstream git repo at tag `v0.137.0`), not pulled
  from a registry.
- Container name: `cerid-ms365-mcp`.
- Internal address: `http://cerid-ms365-mcp:3000/mcp`.
- Transport: streamable-HTTP.
- AuthN to the MCP server: **none**. This doc claimed a static-bearer
  control (`CERID_CONNECTORS_BEARER`) until 2026-08-10; the server has no
  such check. It forwards the client's bearer straight to Microsoft Graph
  rather than validating it, which is why Cerid's static hex token arrives
  there as an invalid JWT. The container's protection is that it binds to
  loopback only (`CERID_BIND_ADDR`). The bearer is still required on the
  Cerid side — `requires_env` gates both Outlook connectors on it — but
  read it as a registration precondition, not a security boundary.
- AuthN to Microsoft: MSAL device-code flow, owned entirely by the
  sibling container. The Cerid backend never sees Microsoft refresh
  tokens.
- Tools called for search: `list-mail-messages` (`GET /me/messages`) and
  `get-calendar-view` (`GET /me/calendarView`, scope `Calendars.Read`).
  Not `search-messages` / `list-calendar-events` — the first does not
  exist and the second takes no date parameters. Mail scope is
  `Mail.Read` until inbox actions are on, then `Mail.ReadWrite`.
  `Mail.Send` is never requested. Filing uses the sibling's category
  update and move tools; a reply draft uses its reply-draft tool. There
  is no send and no delete on this path.

Unlike the Google connector, **no OAuth client setup is required**. The
ms-365-mcp-server image ships with a public MSAL client registration
suitable for personal-use device-code login.

## Operator setup

### 1. Populate `.env`

Add to the repo-root `.env`:

```
CERID_CONNECTORS_BEARER=<openssl rand -hex 32>
```

`CERID_CONNECTORS_BEARER` is any 32-byte hex string. Generate one with
`openssl rand -hex 32`. Only the Cerid backend reads it, and it reads it
at boot — a value changed afterwards does nothing until the backend is
recreated. The sibling container does not check it (see Architecture
above), so there is nothing to keep in sync on that side.

No Microsoft client_id or client_secret is needed; MSAL handles client
identity inside the sibling container.

### 2. Bring up the connector stack

```bash
docker compose \
  -f docker-compose.yml \
  -f stacks/connectors/docker-compose.yml \
  --profile pro up -d
```

The `pro` profile gates the Pro-tier sibling containers so they don't
start in default/free deployments.

### 3. First-use device-code login

```bash
docker compose \
  -f docker-compose.yml \
  -f stacks/connectors/docker-compose.yml \
  --profile pro \
  exec ms365-mcp node dist/index.js --login
```

Three parts of that line were wrong here until 2026-08-10, and each fails
differently:

- **Both compose files.** Naming only the stacks file puts you in compose
  project `connectors`, where the container does not exist — `service
  "ms365-mcp" is not running`.
- **`--profile pro`.** Without it the service is filtered out entirely and
  `compose config` renders `services: {}`.
- **`node dist/index.js --login`.** There is no `ms365-mcp` executable in
  the image — the package `bin` is `ms-365-mcp-server` — and login is a
  *flag*, not a subcommand.

The command prints a short code and a URL. Open
[microsoft.com/devicelogin](https://microsoft.com/devicelogin) in a
browser, paste the code, complete Microsoft sign-in, and grant the
requested scopes. With inbox actions off the sibling asks for `Mail.Read`
and `Calendars.Read`. With `CERID_INBOX_ACTIONS_ENABLED` on, mail is
`Mail.ReadWrite`. `Mail.Send` is not in the grant.

The resulting token is cached to `/data/token-cache.json` inside the
container, backed by the `ms365-mcp-data` named volume, so the login
survives container recreates and rebuilds. There is one Graph token
store. Repeat this step when you revoke the grant, and when you turn
inbox actions on — the router does not start that re-login.

## Enable in Cerid

Nothing to set per-connector. `outlook_connector` (mail) and
`outlook_calendar_sync` (calendar) are derived from the tier — activate a
Pro license, or on a self-hosted install set `CERID_TIER=pro` in `.env`
and recreate the backend.

This section used to say `outlook_connector=true` and
`~~CERID_FEATURE_TIER~~=pro`. Neither did anything: individual flags are not
settable (`_refresh_flags()` recomputes the whole Pro set from the tier), and
`~~CERID_FEATURE_TIER~~` does not exist anywhere in the codebase — the variable is
`CERID_TIER`. Five other Pro docs carried the same non-existent name, two of
them instructing operators to set it in `.env` to unlock a feature; all were
corrected on 2026-08-10.

## What gets ingested

Outlook data is **search-on-demand**, not bulk-indexed. When a chat
question benefits from email context, the connector calls
`list-mail-messages` with an OData `$search` (quoted KQL — Graph rejects
an unquoted value) and `$top`, and the retrieval pipeline treats each
returned message as a citable artifact for that turn only. The local KB
does not retain Outlook bodies between sessions; only message ids and
lightweight metadata may be cached for deduplication.

## Privacy posture

- **Credentials never leave the Mac.** Device-code tokens are issued
  directly to the sibling container and cached on the host volume.
- **The sibling is loopback-only.** Its published port binds to
  `CERID_BIND_ADDR` (127.0.0.1 by default), so it is not reachable from
  outside the host. That binding *is* the control — the bearer is not one,
  because the server never checks it.
- **Refresh tokens stay in `/data/token-cache.json`** (the
  `ms365-mcp-data` volume), never in the Cerid backend's memory or its KB
  stores.
- **Chat search is on demand.** A chat question contacts Microsoft Graph
  only when retrieval asks. Inbox triage is the exception: when both
  gates in `docs/PRO_INBOX_TRIAGE.md` are open, Cerid reads recent unread
  mail on that cadence.

## Inbox actions

Off by default. Set `CERID_INBOX_ACTIONS_ENABLED=true` (or `1`) and
**recreate** the sibling. A restart is not enough. With the flag on, the
container drops `--read-only` and the allowed scopes become
`Mail.ReadWrite` and `Calendars.Read` unless `Mail.ReadWrite` is already
in `MS365_MCP_ALLOWED_SCOPES`. `Mail.Send` is not added.

Filing sets the Outlook category, then moves. The actionable folder and
category name is `Cerid/Action`. The other names are `Cerid/Urgent`,
`Cerid/Personal`, `Cerid/Newsletter`, and `Cerid/Promo`. Junk and Deleted
Items are not destinations.

List results copy categories, and a well-known folder (`inbox`,
`archive`, `drafts`, `sentitems`), onto the message when Graph includes
them. A folder id that is a GUID is not copied. A correction is learned
only when that metadata is present, and the unread triage fetch often
will not see a message you already archived.

Recreate the backend after the flag change so it reads the new
environment. `docker compose restart` is not enough:

```bash
docker compose up -d --no-deps mcp-server
```

Re-consent with the §3 login after the sibling has been recreated.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Boot log: `CERID_CONNECTORS_BEARER unset — Pro cloud connectors not registered` | Set the env var in `.env`, then **recreate** the backend: `docker compose up -d --no-deps mcp-server`. A plain `docker compose restart` keeps the environment baked in at create time and changes nothing. |
| `--login` says "already authenticated" but queries still fail | Token expired and silent refresh was rejected. Most often `MS365_MCP_TENANT_ID` is unset: "common" fails silent refresh for personal Microsoft accounts, so the first login works and every renewal after it does not. Set it to `consumers` for a personal account, or the tenant id for work/school, then run the §3 command with `--logout` and again with `--login`. |
| `breaker-open` errors in `/health` referencing `ms365-mcp` | Sibling container has crashed or is restarting. Check `docker logs cerid-ms365-mcp`. |
| Device-code URL returns "AADSTS50020" | You're signing in with a tenant account whose admin has blocked the public MSAL client. Use a personal Microsoft account, or have your tenant admin register an internal app and configure it via the image's documented overrides. |
| Graph answers 401 / `InvalidAuthenticationToken` | Not a bearer mismatch — the sibling does not check the bearer, it forwards it to Graph, so Cerid's static hex token is what Graph rejects when no device-code login has been completed. Run the §3 login. |
| Queries return zero results with no error | The sibling reports an unknown tool or a dropped parameter as a normal result carrying `isError`, not an exception. Confirm the tool names are `list-mail-messages` / `get-calendar-view` and that `MS365_MCP_ALLOWED_SCOPES` is **whitespace**-separated — a comma-separated value is read as one scope that matches nothing, and every mail and calendar tool is filtered out. |
| Login token disappears after `docker compose down -v` | The `-v` flag drops the `ms365-mcp-data` volume. Re-run the §3 login to re-issue the device code. |
| Actions flag is set and Graph still answers 403 on a category or move | The sibling is still `--read-only`, or the token is still `Mail.Read`. Recreate the sibling, then repeat the §3 login. A restart keeps the old command. |
