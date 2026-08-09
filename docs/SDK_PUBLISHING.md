# SDK Publishing Runbook — `cerid-sdk`

Operator runbook for cutting releases of the two `cerid-sdk` client
packages: the Python SDK at
[`packages/sdk/python/`](../packages/sdk/python/) (→ PyPI) and the
TypeScript SDK at
[`packages/sdk/typescript/`](../packages/sdk/typescript/) (→ npm).

Both release pipelines use OIDC trusted publishing — no long-lived API
token lives anywhere for either registry. Both require **operator-only,
one-time registry-side setup** (creating the trusted-publisher binding)
before the first release of either SDK can fire; everything under
"Per-release flow" is safe for anyone with repo write access to run.

---

# Python SDK (PyPI)

The release pipeline is
[`.github/workflows/release-sdk-python.yml`](../.github/workflows/release-sdk-python.yml).
Auth uses **PyPI Trusted Publishing** (OIDC) — no long-lived API
token lives anywhere. The PyPI side must be configured once before the
first release fires.

---

## One-time setup

Done by an account with publish rights on PyPI / TestPyPI and admin
rights on the GitHub repo.

### 1. Create the GitHub environments

GitHub → repo → Settings → Environments → New environment.

Create two environments with the exact names below (the workflow's
`environment.name` expression resolves to one of these per run):

- `pypi`
- `testpypi`

Optional but recommended: add `Required reviewers` to the `pypi`
environment so a human approves every production publish.

### 2. Configure PyPI Trusted Publisher

PyPI → log in → manage account or project page → "Publishing".

The trusted publisher is already configured on PyPI for `cerid-sdk`
(repo `Cerid-AI/cerid-ai`, workflow `release-sdk-python.yml`,
environment `pypi`); releases publish tokenlessly. For a **new**
package, register a **pending publisher** first (no project exists yet
on PyPI). The binding config:

| Field | Value |
|---|---|
| PyPI project name | `cerid-sdk` |
| Owner | `Cerid-AI` (or current org/user) |
| Repository name | `cerid-ai` |
| Workflow filename | `release-sdk-python.yml` |
| Environment name | `pypi` |

Repeat on **TestPyPI** with environment name `testpypi`. Same form,
different host.

### 3. Smoke-test the dry-run path

Before tagging anything, fire the workflow manually:

GitHub → Actions → "Release / cerid-sdk (Python)" → Run workflow →
target = `testpypi`.

The workflow will build, test, and publish `cerid-sdk==<current
pyproject version>` to TestPyPI. Verify the page renders correctly
at <https://test.pypi.org/p/cerid-sdk> and that the README + classifiers
look right.

If the version on PyPI/TestPyPI already exists, **bump the version in
[`packages/sdk/python/pyproject.toml`](../packages/sdk/python/pyproject.toml)
first** — PyPI rejects re-uploads of an existing version (immutable
release contract).

---

## Per-release flow

### 1. Bump the version

Edit
[`packages/sdk/python/pyproject.toml`](../packages/sdk/python/pyproject.toml):

```diff
[project]
 name = "cerid-sdk"
-version = "0.1.1"
+version = "0.1.2"
```

If the wire protocol shifted, also bump
[`packages/sdk/python/src/cerid/__version__.py`](../packages/sdk/python/src/cerid/__version__.py):

```python
SDK_PROTOCOL_VERSION = "1.1.1"
```

`SDK_PROTOCOL_VERSION` and the package `version` are independent —
the former tracks the server's `/sdk/v1/` contract, the latter the
client library's release cadence. Bump both when the contract
changes; bump only the package version for client-only fixes.

### 2. Commit + tag

```bash
git add packages/sdk/python/pyproject.toml
git commit -m "cerid-sdk: bump to 0.1.2"
git tag cerid-sdk-v0.1.2
git push origin main
git push origin cerid-sdk-v0.1.2
```

The tag pattern **must** be `cerid-sdk-v<version>` exactly — the
workflow asserts the suffix matches the pyproject version and fails
the release if not.

### 3. Watch the workflow

The tag push triggers `release-sdk-python.yml`. Steps:

1. Assert tag version matches `pyproject.toml` version
2. Install package + test deps
3. Run `pytest packages/sdk/python/tests/`
4. Build sdist + wheel
5. `twine check dist/*`
6. Publish via PyPI Trusted Publisher OIDC

If you added required reviewers to the `pypi` environment, the
publish step will pause for approval. Approve → publish → done.

### 4. Verify

- <https://pypi.org/p/cerid-sdk> shows the new version
- `pip install cerid-sdk==<version>` works in a clean venv
- The README renders correctly on the project page

---

## Dry-run / pre-release flow

Use `workflow_dispatch` with `target=testpypi` whenever you want to
test the pipeline without touching real PyPI:

```bash
gh workflow run release-sdk-python.yml -f target=testpypi
```

TestPyPI is a separate index — `pip install` from it requires the
explicit index URL:

```bash
pip install --index-url https://test.pypi.org/simple/ cerid-sdk==<version>
```

---

## Rollback / yanking

PyPI does not allow deleting a published version. If a release is
broken:

1. **Yank** the bad version on the PyPI project page → Manage →
   Releases → Yank. Yanking hides the version from
   `pip install cerid-sdk` (without a version pin) but preserves
   reproducibility for anyone already pinned to it.
2. Bump the version in `pyproject.toml` to the next patch (`0.1.3`),
   land the fix, tag, push.

Never reuse a yanked version number.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Workflow fails "Tag version does not match pyproject" | Tag suffix ≠ pyproject version | Re-tag with correct suffix; or bump pyproject and re-tag |
| Publish step fails "Trusted publisher not configured" | PyPI side missing the trust binding | Repeat one-time setup step 2 — match repo, workflow filename, environment name exactly |
| Publish step fails "version already exists" | Version already on PyPI (immutable) | Bump the version, re-tag |
| Test step fails on missing `jsonschema` | SDK's `[test]` extra not installed | Fixed in the workflow — it installs the package with the `[test]` extra; if reproducing locally, `pip install -e "packages/sdk/python[test]"` |
| `twine check` flags README rendering | Markdown syntax PyPI doesn't render | Validate locally with `twine check dist/*` after `python -m build` |

---

# TypeScript SDK (npm)

The release pipeline is
[`.github/workflows/release-sdk-typescript.yml`](../.github/workflows/release-sdk-typescript.yml),
mirroring the Python workflow's structure and trigger shape. Auth uses
**npm Trusted Publishing** (OIDC) — no long-lived `NPM_TOKEN` lives
anywhere. The npm side must be configured once before the first
release fires.

---

## One-time setup — **operator-only**

Done by an account with publish rights on the `@cerid-ai` npm org and
admin rights on the GitHub repo. Nobody else can complete this section
— it requires credentials this runbook does not and should not grant
to an agent or CI job.

### 1. Create the GitHub environment

GitHub → repo → Settings → Environments → New environment.

Create one environment named exactly:

- `npm`

Optional but recommended: add `Required reviewers` to the `npm`
environment so a human approves every production publish.

Unlike the Python workflow's `pypi`/`testpypi` split, the TypeScript
workflow's `dry-run` target never touches the npm registry — it runs
`npm pack` locally and uploads no artifact — so it needs no separate
environment or trusted-publisher binding.

### 2. Configure the npm Trusted Publisher

npmjs.com → sign in → the `@cerid-ai/sdk` package page (or org
settings, for the first-ever publish) → "Trusted Publishers" → add a
GitHub Actions publisher:

| Field | Value |
|---|---|
| npm package name | `@cerid-ai/sdk` |
| Organization / user | `Cerid-AI` (or current org/user) |
| Repository name | `cerid-ai` |
| Workflow filename | `release-sdk-typescript.yml` |
| Environment name | `npm` |

npm's trusted-publisher UI requires the package to already exist for
most flows; `@cerid-ai/sdk` was bootstrapped with one manual
`npm publish --access public` from an authenticated maintainer
account, and every subsequent release goes through OIDC.

**Requirement:** npm OIDC trusted publishing requires **npm >= 11.5.1**;
the workflow upgrades npm before publishing. Node 22 bundles npm 10,
which silently publishes unauthenticated and gets a masked 404.

### 3. Smoke-test the dry-run path

Before tagging anything, fire the workflow manually:

GitHub → Actions → "Release / cerid-sdk (TypeScript)" → Run workflow →
target = `dry-run`.

The workflow builds, typechecks, tests, and runs `npm pack` — inspect
the uploaded tarball listing in the job log and confirm it matches the
`npm pack --dry-run` output below (only `dist/`, `LICENSE`,
`package.json`).

If the version on npm already exists, **bump the version in
[`packages/sdk/typescript/package.json`](../packages/sdk/typescript/package.json)
first** — npm rejects re-publishing an existing version (immutable
release contract, same as PyPI).

---

## Per-release flow

### 1. Bump the version

Edit
[`packages/sdk/typescript/package.json`](../packages/sdk/typescript/package.json):

```diff
 {
   "name": "@cerid-ai/sdk",
-  "version": "0.1.1",
+  "version": "0.1.2",
```

If the wire protocol shifted, also bump `SDK_PROTOCOL_VERSION` in the
Python SDK's `__version__.py` (the TypeScript client doesn't carry a
separate protocol constant; it's kept in sync with the Python SDK's
by the `sdk-contract` CI gate testing both against the same
`docs/openapi-sdk-v1.json`).

### 2. Commit + tag

```bash
git add packages/sdk/typescript/package.json
git commit -m "cerid-sdk-ts: bump to 0.1.2"
git tag cerid-sdk-ts-v0.1.2
git push origin main
git push origin cerid-sdk-ts-v0.1.2
```

The tag pattern **must** be `cerid-sdk-ts-v<version>` exactly — the
workflow asserts the suffix matches the `package.json` version and
fails the release if not. Note the `-ts-` infix: it's what
distinguishes a TypeScript SDK tag from the Python SDK's
`cerid-sdk-v*` pattern so the two release workflows never both fire
off the same tag push.

### 3. Watch the workflow

The tag push triggers `release-sdk-typescript.yml`. Steps:

1. Assert tag version matches `package.json` version
2. `npm ci`
3. `npm run typecheck`
4. `npm test`
5. `npm run build`
6. `npm pack --dry-run` (sanity-check the artifact listing in the log)
7. Publish via npm Trusted Publisher OIDC (`npm publish --provenance
   --access public`) — `prepublishOnly` in `package.json` re-runs
   build + typecheck + test as a final guard even though the workflow
   already ran them explicitly

If you added required reviewers to the `npm` environment, the publish
step will pause for approval. Approve → publish → done.

### 4. Verify — **the publish itself is operator-only; verification is not**

- <https://www.npmjs.com/package/@cerid-ai/sdk> shows the new version
- `npm view @cerid-ai/sdk version` matches
- `npm install @cerid-ai/sdk@<version>` works in a clean project

---

## Dry-run / pre-release flow

Use `workflow_dispatch` with `target=dry-run` whenever you want to
test the pipeline without touching the real npm registry:

```bash
gh workflow run release-sdk-typescript.yml -f target=dry-run
```

Locally, the same check without CI:

```bash
cd packages/sdk/typescript
npm ci && npm run typecheck && npm test && npm run build
npm pack --dry-run
```

`npm pack --dry-run` never authenticates or writes to any registry —
it only lists what a real publish would upload. Safe to run any time,
by anyone, including in an agent session.

---

## Rollback / yanking

npm does not allow deleting a published version outright (registry
policy blocks unpublish after 72 hours, and even within that window
unpublishing is discouraged for anything with downstream consumers).
If a release is broken:

1. **Deprecate** the bad version: `npm deprecate @cerid-ai/sdk@0.1.2
   "broken — use 0.1.3"`. This is reversible and doesn't break
   existing installs pinned to that version.
2. Bump the version in `package.json` to the next patch (`0.1.3`),
   land the fix, tag, push.

Never reuse a deprecated or unpublished version number.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Workflow fails "Tag version does not match package.json" | Tag suffix ≠ `package.json` version | Re-tag with correct suffix; or bump `package.json` and re-tag |
| Publish step fails with an OIDC/auth error | npm side missing the trust binding | Repeat one-time setup step 2 — match repo, workflow filename, environment name exactly |
| Publish step fails "cannot publish over previously published version" | Version already on npm (immutable) | Bump the version, re-tag |
| Publish "succeeds" but the version never appears, or fails with a masked 404 | npm 10 (bundled with Node 22) doesn't support OIDC trusted publishing and silently publishes unauthenticated | Upgrade npm to >= 11.5.1 before `npm publish` — the workflow does this automatically |
| `npm pack --dry-run` lists `src/` or `tests/` | `files` field in `package.json` missing or wrong | Confirm `"files": ["dist"]` is present; npm always includes `LICENSE` + `package.json` regardless |

---

## Compatibility with the server-side drift gate

The server-side
[`sdk-openapi-drift`](../.github/workflows/ci.yml) CI job enforces
that `/sdk/v1/` doesn't drift from the committed baseline at
[`docs/openapi-sdk-v1.json`](openapi-sdk-v1.json). When either SDK
ships a new release, the server's `SDK_VERSION`
([`app/routers/sdk_version.py`](../src/mcp/app/routers/sdk_version.py))
should match `SDK_PROTOCOL_VERSION` in the Python client (the source
of truth both SDKs are tested against). CI catches the divergence
before merge — the publish workflows trust that contract and do not
re-check.
