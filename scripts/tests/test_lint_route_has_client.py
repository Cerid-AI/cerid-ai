# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Red/green probes for scripts/lint-route-has-client.py (audit Gate 6).

The load-bearing case is the reachability audit's own finding class: a
FastAPI route can be registered, tested, and dead — no component, hook,
SDK client, or desktop bridge ever requests it. A route with a passing
backend test looks referenced to any coverage-based check; only a
cross-tier literal-path scan catches "server implements it, nobody calls
it" (tasks/2026-08-11-reachability-audit.md: alerts, webhook
subscriptions, migration, kb_admin diagnostics).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_route_has_client", _ROOT / "scripts" / "lint-route-has-client.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_route_has_client"] = lint
_SPEC.loader.exec_module(lint)


def _router_file(tmp_path: Path, name: str, content: str) -> Path:
    routers = tmp_path / "routers"
    routers.mkdir(parents=True, exist_ok=True)
    f = routers / name
    f.write_text(content, encoding="utf-8")
    return routers


def _client_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    client_root = tmp_path / "web"
    for rel, content in files.items():
        f = client_root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return client_root


def _run(
    router_dir: Path,
    client_dir: Path,
    tmp_path: Path,
    allow_lines: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> int:
    allowlist = tmp_path / "allow.txt"
    if allow_lines is not None:
        allowlist.write_text("\n".join(allow_lines) + "\n", encoding="utf-8")
    argv = [
        "--check",
        "--router-dir", str(router_dir),
        "--client-dir", str(client_dir),
        "--allowlist", str(allowlist),
        "--rel-root", str(router_dir),
    ]
    if extra_args:
        argv += extra_args
    return lint.main(argv)


class TestRedCases:
    """Each must exit 1 — a gate never seen failing is not a gate."""

    def test_new_unwired_route_fails(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1

    def test_test_only_backend_caller_does_not_count(self, tmp_path):
        """The reachability audit's core lesson: a route with a passing
        backend test file, but no reference under the client dirs, is
        still unwired — this gate only scans the client surfaces, so a
        backend test string alone (outside client_dir) cannot flip it green."""
        router_dir = _router_file(
            tmp_path,
            "alerts.py",
            'router = APIRouter(prefix="/observability/alerts", tags=["alerts"])\n\n'
            '@router.post("/evaluate")\n'
            "async def trigger_evaluation():\n"
            "    return []\n",
        )
        # "client" dir deliberately does NOT reference the route — mirrors
        # the real alerts.py finding where only a backend test calls it.
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1

    def test_stale_violation_entry_fails(self, tmp_path):
        """An allowlisted VIOLATION whose route now HAS a client must be
        flagged so the allowlist ratchets down, not just accumulates."""
        router_dir = _router_file(
            tmp_path,
            "kb_admin.py",
            'router = APIRouter(tags=["kb-admin"])\n\n'
            '@router.post("/admin/kb/reindex")\n'
            "async def reindex_corpus():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path, {"src/lib/kb.ts": 'fetch(`${MCP_BASE}/admin/kb/reindex`)\n'}
        )
        key = "kb_admin.py::reindex_corpus::POST /admin/kb/reindex"
        assert (
            _run(
                router_dir,
                client_dir,
                tmp_path,
                allow_lines=[f"VIOLATION|{key}|reachability audit 2026-08-11"],
            )
            == 1
        )

    def test_route_removed_stale_entry_fails(self, tmp_path):
        router_dir = _router_file(
            tmp_path, "empty.py", 'router = APIRouter(tags=["empty"])\n'
        )
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        assert (
            _run(
                router_dir,
                client_dir,
                tmp_path,
                allow_lines=["VIOLATION|gone.py::x::GET /gone|no longer real"],
            )
            == 1
        )

    def test_malformed_allowlist_entry_leaves_route_unallowlisted(self, tmp_path):
        """A line missing the reason field parses to nothing — the route
        it was meant to cover stays flagged as new debt, not silently passed."""
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        assert (
            _run(
                router_dir,
                client_dir,
                tmp_path,
                allow_lines=["VIOLATION|widgets.py::get_orphan::GET /widgets/orphan"],
            )
            == 1
        )

    def test_markdown_only_mention_does_not_satisfy(self, tmp_path):
        """A route path written into a README/TODO/.md note is not a
        caller. .md is excluded from the client scan entirely — this is
        the documentation-laundering bypass the consolidated audit names
        as its dominant failure mode, and must stay closed."""
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {
                "src/App.tsx": "export const x = 1\n",
                "src/NOTES.md": "TODO: wire up `/widgets/orphan` from the client\n",
            },
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1

    def test_comment_only_mention_does_not_satisfy(self, tmp_path):
        """A route path mentioned only in a comment (whole-line, or a
        block comment) is not a caller — only code counts."""
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {
                "src/App.tsx": (
                    "// TODO: wire /widgets/orphan into the settings pane\n"
                    "/*\n"
                    " * also mentioned here: /widgets/orphan\n"
                    " */\n"
                    "export const x = 1\n"
                ),
                "src/scratch.py": "# scratch note: /widgets/orphan\n",
            },
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1


class TestLaunderingHardening:
    """Red/green pairs for the specific laundering vectors an adversarial
    review demonstrated against an earlier version of this gate: a route
    with zero real callers must stay RED even when its literal path text
    appears somewhere in the client tree via dead code, an unreachable
    branch, or test/mock scaffolding."""

    def test_dead_unreferenced_const_does_not_satisfy(self, tmp_path):
        """The exact exploit demonstrated in review: a route literal
        assigned to a const that is never imported or used anywhere must
        NOT count as a caller."""
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {
                "src/App.tsx": "export const x = 1\n",
                "src/dead.ts": 'const DEAD_ROUTE = "/widgets/orphan"\n',
            },
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1

    def test_referenced_const_via_template_still_satisfies(self, tmp_path):
        """Regression guard: a const that IS used elsewhere via a
        ``${NAME}`` template (the real-world BASE-URL pattern) must keep
        satisfying the gate — the dead-const mask must not blank a live
        declaration."""
        router_dir = _router_file(
            tmp_path,
            "graph_tour.py",
            'router = APIRouter(prefix="/graph/tour", tags=["graph"])\n\n'
            '@router.post("/generate")\n'
            "async def generate_tour():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {
                "src/lib/tour.ts": (
                    'const BASE = "/graph/tour"\n'
                    "export function generateTour() {\n"
                    "  return fetch(`${BASE}/generate`)\n"
                    "}\n"
                )
            },
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 0

    def test_dead_if_false_branch_does_not_satisfy(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {
                "src/App.tsx": "export const x = 1\n",
                "src/dead.ts": (
                    "if (false) {\n"
                    '  fetch("/widgets/orphan")\n'
                    "}\n"
                ),
            },
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1

    def test_test_directory_reference_does_not_satisfy(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {
                "src/App.tsx": "export const x = 1\n",
                "src/__tests__/widgets.test.ts": 'fetch("/widgets/orphan")\n',
            },
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1

    def test_router_file_outside_conventional_subdir_is_scanned(self, tmp_path):
        """The router-dir walk must be recursive regardless of the
        containing subdirectory's name — mirrors the real gap where
        app/processor/router.py lived outside app/routers/."""
        nested = tmp_path / "routers" / "processor"
        nested.mkdir(parents=True)
        (nested / "router.py").write_text(
            'router = APIRouter(prefix="/processor", tags=["processor"])\n\n'
            '@router.get("/status")\n'
            "async def processor_status():\n"
            "    return {}\n",
            encoding="utf-8",
        )
        router_dir = tmp_path / "routers"
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 1
        client_dir = _client_tree(
            tmp_path, {"src/lib/processor.ts": 'fetch("/processor/status")\n'}
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 0


class TestGreenCases:
    """Each must exit 0 — the matcher features the real tree depends on."""

    def test_literal_path_match_passes(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "plugins.py",
            'router = APIRouter(tags=["plugins"])\n\n'
            '@router.post("/plugins/scan")\n'
            "async def scan_plugins():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {"src/lib/settings-registry/extensions.ts": '  path: "/plugins/scan",\n'},
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 0

    def test_path_param_wildcard_matches_interpolation(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "kb_admin.py",
            'router = APIRouter(tags=["kb-admin"])\n\n'
            '@router.post("/admin/artifacts/{artifact_id}/reingest")\n'
            "async def reingest(artifact_id: str):\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path,
            {
                "src/lib/kb.ts": (
                    "export function reIngestArtifact(id: string) {\n"
                    "  return fetch(`${MCP_BASE}/admin/artifacts/${id}/reingest`)\n"
                    "}\n"
                )
            },
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 0

    def test_prefix_plus_path_resolves(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "migration.py",
            'router = APIRouter(prefix="/api/migrate", tags=["migration"])\n\n'
            '@router.post("/notion")\n'
            "async def migrate_notion():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path, {"src/lib/migration.ts": 'fetch(`${MCP_BASE}/api/migrate/notion`)\n'}
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 0

    def test_allowlisted_violation_passes(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "alerts.py",
            'router = APIRouter(prefix="/observability/alerts", tags=["alerts"])\n\n'
            '@router.post("/evaluate")\n'
            "async def trigger_evaluation():\n"
            "    return []\n",
        )
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        key = "alerts.py::trigger_evaluation::POST /observability/alerts/evaluate"
        assert (
            _run(
                router_dir,
                client_dir,
                tmp_path,
                allow_lines=[f"VIOLATION|{key}|reachability audit 2026-08-11"],
            )
            == 0
        )

    def test_allowlisted_api_only_passes(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "sdk.py",
            'router = APIRouter(prefix="/sdk/v1", tags=["SDK"])\n\n'
            '@router.post("/ingest/webhook/{token}")\n'
            "async def sdk_ingest_webhook(token: str):\n"
            "    return {}\n",
        )
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        key = "sdk.py::sdk_ingest_webhook::POST /sdk/v1/ingest/webhook/{token}"
        assert (
            _run(
                router_dir,
                client_dir,
                tmp_path,
                allow_lines=[f"API-ONLY|{key}|inbound webhook receiver by design"],
            )
            == 0
        )

    def test_suppression_comment_exempts_route(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")  # route-has-client-allowed: probe-only, never shipped\n'
            "async def get_orphan():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 0

    def test_no_prefix_router_uses_bare_path(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "health.py",
            'router = APIRouter()\n\n'
            '@router.get("/health/ping")\n'
            "async def health_ping():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(
            tmp_path, {"src/lib/health.ts": 'fetch(`${MCP_BASE}/health/ping`)\n'}
        )
        assert _run(router_dir, client_dir, tmp_path, allow_lines=[]) == 0

    def test_update_reseeds_violation_and_keeps_api_only(self, tmp_path):
        router_dir = _router_file(
            tmp_path,
            "widgets.py",
            'router = APIRouter(prefix="/widgets", tags=["widgets"])\n\n'
            '@router.get("/orphan")\n'
            "async def get_orphan():\n"
            "    return {}\n"
            '@router.get("/probe")\n'
            "async def get_probe():\n"
            "    return {}\n",
        )
        client_dir = _client_tree(tmp_path, {"src/App.tsx": "export const x = 1\n"})
        allowlist = tmp_path / "allow.txt"
        api_key = "widgets.py::get_probe::GET /widgets/probe"  # pragma: allowlist secret
        allowlist.write_text(f"API-ONLY|{api_key}|infra probe, kept by design\n", encoding="utf-8")
        rc = lint.main(
            [
                "--update",
                "--router-dir", str(router_dir),
                "--client-dir", str(client_dir),
                "--allowlist", str(allowlist),
                "--rel-root", str(router_dir),
            ]
        )
        assert rc == 0
        allow = lint._load_allowlist(allowlist)
        assert allow[api_key][0] == "API-ONLY"
        new_key = "widgets.py::get_orphan::GET /widgets/orphan"
        assert allow[new_key][0] == "VIOLATION"
        check_rc = lint.main(
            [
                "--check",
                "--router-dir", str(router_dir),
                "--client-dir", str(client_dir),
                "--allowlist", str(allowlist),
                "--rel-root", str(router_dir),
            ]
        )
        assert check_rc == 0
