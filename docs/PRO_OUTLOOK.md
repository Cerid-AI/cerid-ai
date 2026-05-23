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

- Image: `softeria/ms-365-mcp-server`, pinned by git commit SHA in
  `stacks/connectors/docker-compose.yml`.
- Container name: `cerid-ms365-mcp`.
- Internal address: `http://cerid-ms365-mcp:3000/mcp`.
- Transport: streamable-HTTP.
- AuthN to the MCP server: static bearer token (`CERID_CONNECTORS_BEARER`).
- AuthN to Microsoft: MSAL device-code flow, owned entirely by the
  sibling container. The Cerid backend never sees Microsoft refresh
  tokens.

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
`openssl rand -hex 32`. The same token must be present when the Cerid
backend boots and when the sibling MCP container boots — they share it
out of band via the `.env` file.

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
  -f stacks/connectors/docker-compose.yml \
  exec ms365-mcp ms365-mcp login
```

The command prints a short code and a URL. Open
[microsoft.com/devicelogin](https://microsoft.com/devicelogin) in a
browser, paste the code, complete Microsoft sign-in, and grant the
requested Mail/Calendar read scopes.

The resulting token is cached to `/data/token-cache.json` inside the
container, which is bind-mounted to a host volume so the login survives
container recreates and rebuilds. You do not need to repeat this step
unless you revoke the grant from your Microsoft account settings.

## Enable in Cerid

Set the feature flag:

```
outlook_connector=true
```

Operators on the Pro tier (`CERID_FEATURE_TIER=pro`) get this flag flipped
on automatically — no manual override needed.

## What gets ingested

Outlook data is **search-on-demand**, not bulk-indexed. When a chat
question benefits from email context, the connector runs a Microsoft
Graph search query, returns the top-N matching messages, and the
retrieval pipeline treats each message as a citable artifact for that
turn only. The local KB does not retain Outlook bodies between sessions;
only message ids and lightweight metadata may be cached for
deduplication.

## Privacy posture

- **Credentials never leave the Mac.** Device-code tokens are issued
  directly to the sibling container and cached on the host volume.
- **Bearer token is local-network only.** `CERID_CONNECTORS_BEARER` gates
  the sibling MCP container on the Docker bridge network; it is not
  reachable from outside the host.
- **Refresh tokens stay in `/data/token-cache.json`** (bind-mounted),
  never in the Cerid backend's memory or its KB stores.
- **No background polling.** The connector only contacts Microsoft Graph
  when a user query triggers it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Boot log: `CERID_CONNECTORS_BEARER unset — Pro cloud connectors not registered` | Set the env var in `.env`, then `docker compose up -d` again. |
| `ms365-mcp login` says "already authenticated" but queries still fail | Token expired and refresh was rejected. Run `ms365-mcp logout` followed by `ms365-mcp login`. |
| `breaker-open` errors in `/health` referencing `ms365-mcp` | Sibling container has crashed or is restarting. Check `docker logs cerid-ms365-mcp`. |
| Device-code URL returns "AADSTS50020" | You're signing in with a tenant account whose admin has blocked the public MSAL client. Use a personal Microsoft account, or have your tenant admin register an internal app and configure it via the image's documented overrides. |
| 401 from MCP server in backend logs | Bearer token mismatch between the backend's env and the sibling's env. Confirm both containers were recreated after the last `.env` edit. |
| Login token disappears after `docker compose down -v` | The `-v` flag drops the bind-mounted volume. Re-run `ms365-mcp login` to re-issue the device code. |
