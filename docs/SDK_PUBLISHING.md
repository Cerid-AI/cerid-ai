# SDK Publishing Runbook — `cerid-sdk` (Python)

Operator runbook for cutting a release of the Python SDK at
[`packages/sdk/python/`](../packages/sdk/python/) to PyPI.

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

For the **first** release of a project, register a **pending publisher**
(no project exists yet on PyPI). Subsequent releases reuse the same
config:

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
-version = "0.1.0"
+version = "0.1.1"
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
git commit -m "cerid-sdk: bump to 0.1.1"
git tag cerid-sdk-v0.1.1
git push origin main
git push origin cerid-sdk-v0.1.1
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
2. Bump the version in `pyproject.toml` to the next patch (`0.1.2`),
   land the fix, tag, push.

Never reuse a yanked version number.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Workflow fails "Tag version does not match pyproject" | Tag suffix ≠ pyproject version | Re-tag with correct suffix; or bump pyproject and re-tag |
| Publish step fails "Trusted publisher not configured" | PyPI side missing the trust binding | Repeat one-time setup step 2 — match repo, workflow filename, environment name exactly |
| Publish step fails "version already exists" | Version already on PyPI (immutable) | Bump the version, re-tag |
| `twine check` flags README rendering | Markdown syntax PyPI doesn't render | Validate locally with `twine check dist/*` after `python -m build` |

---

## Compatibility with the server-side drift gate

The server-side
[`sdk-openapi-drift`](../.github/workflows/ci.yml) CI job enforces
that `/sdk/v1/` doesn't drift from the committed baseline at
[`docs/openapi-sdk-v1.json`](openapi-sdk-v1.json). When the SDK ships
a new release, the server's `SDK_VERSION`
([`app/routers/sdk_version.py`](../src/mcp/app/routers/sdk_version.py))
should match `SDK_PROTOCOL_VERSION` in the client. CI catches the
divergence before merge — the publish workflow trusts that contract
and does not re-check.
