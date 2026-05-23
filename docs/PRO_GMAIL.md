# Pro Connector — Gmail

## What this connector does

The Gmail Pro connector lets chat answers cite messages from your Gmail
mailbox alongside local KB artifacts. When a question would benefit from
recent email context (e.g. "what did the vendor quote last week?"), the
connector searches Gmail on demand, pulls the most-relevant threads, and
hands them to the retrieval pipeline as first-class evidence with proper
provenance (sender, subject, timestamp, message-id).

Nothing is bulk-indexed for v1 — search-on-demand only. The mailbox stays
where it is; only the matching results enter the answer context window.

## Architecture

The connector runs as a **sibling MCP server** in its own Docker container,
not inside the Cerid backend:

- Image: `taylorwilsdon/google_workspace_mcp`, pinned by git commit SHA in
  `stacks/connectors/docker-compose.yml`.
- Container name: `cerid-google-workspace-mcp`.
- Internal address: `http://cerid-google-workspace-mcp:8000/mcp`.
- Transport: streamable-HTTP.
- AuthN to the MCP server: static bearer token (`CERID_CONNECTORS_BEARER`).
- AuthN to Google: OAuth 2.0, owned entirely by the sibling container.
  The Cerid backend never sees Google refresh tokens.

The Cerid backend registers the sibling server as a remote MCP endpoint
at startup if both the bearer token and the OAuth client credentials are
present in the environment. Missing either one disables the connector
with a clear log line rather than failing the boot.

## Operator setup

### 1. Create the Google OAuth client

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or reuse an existing personal one).
3. Enable the **Gmail API** under *APIs & Services → Library*.
4. Under *APIs & Services → Credentials*, create an **OAuth 2.0 Client ID**
   of type **Desktop app**.
5. Copy the resulting **client_id** and **client_secret**.

### 2. Populate `.env`

Add to the repo-root `.env`:

```
GOOGLE_OAUTH_CLIENT_ID=<paste-from-google-cloud>
GOOGLE_OAUTH_CLIENT_SECRET=<paste-from-google-cloud>
CERID_CONNECTORS_BEARER=<openssl rand -hex 32>
```

`CERID_CONNECTORS_BEARER` is any 32-byte hex string. Generate one with
`openssl rand -hex 32`. The same token must be present when the Cerid
backend boots and when the sibling MCP container boots — they share it
out of band via the `.env` file.

### 3. Bring up the connector stack

```bash
docker compose \
  -f docker-compose.yml \
  -f stacks/connectors/docker-compose.yml \
  --profile pro up -d
```

The `pro` profile gates the Pro-tier sibling containers so they don't
start in default/free deployments.

### 4. First-use OAuth handshake

Open `http://localhost:8810/oauth/start` in a browser on the host machine.
Complete the Google sign-in flow; consent to the Gmail read scopes. The
OAuth refresh token is written to the container's persistent volume and
survives restarts. You do not need to repeat this step unless you revoke
access from your Google account settings.

## Enable in Cerid

Set the feature flag:

```
gmail_connector=true
```

Operators on the Pro tier (`CERID_FEATURE_TIER=pro`) get this flag flipped
on automatically — no manual override needed.

## What gets ingested

Gmail data is **search-on-demand**, not bulk-indexed. When a chat question
benefits from email context, the connector runs the equivalent of a Gmail
search query, returns the top-N matching threads, and the retrieval
pipeline treats each thread as a citable artifact for that turn only.
The local KB does not retain Gmail bodies between sessions; only message
ids and lightweight metadata may be cached for deduplication.

## Privacy posture

- **Credentials never leave the Mac.** The OAuth client_id/secret and the
  refresh token live entirely on the host (in `.env` and the sibling
  container's volume respectively).
- **Bearer token is local-network only.** `CERID_CONNECTORS_BEARER` gates
  the sibling MCP container on the Docker bridge network; it is not
  reachable from outside the host.
- **Refresh tokens stay in the container volume,** never in the Cerid
  backend's memory or its KB stores.
- **No background polling.** The connector only contacts Google when a
  user query triggers it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Boot log: `CERID_CONNECTORS_BEARER unset — Pro cloud connectors not registered` | Set the env var in `.env`, then `docker compose up -d` again. |
| Boot log: `GOOGLE_OAUTH_CLIENT_ID missing — google-workspace-mcp disabled` | Add both `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` and recreate the sibling container so it picks up the new env. |
| `breaker-open` errors in `/health` referencing `google-workspace-mcp` | Sibling container has crashed or is restarting. Check `docker logs cerid-google-workspace-mcp`. |
| `http://localhost:8810/oauth/start` returns 404 | Sibling container is not running, or the `pro` profile was not passed. Re-run the compose command with `--profile pro`. |
| 401 from MCP server in backend logs | Bearer token mismatch between the backend's env and the sibling's env. Confirm both containers were recreated after the last `.env` edit. |
