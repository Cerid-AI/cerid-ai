#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI - Knowledge Base Sync CLI

Sync knowledge base data between machines via Dropbox/iCloud.

Usage:
    python scripts/cerid-sync.py export                          # full export
    python scripts/cerid-sync.py export --since 2026-03-01       # incremental export
    python scripts/cerid-sync.py export --domains code finance   # selective domains
    python scripts/cerid-sync.py import                          # non-destructive merge
    python scripts/cerid-sync.py import --force                  # overwrite local data
    python scripts/cerid-sync.py import --conflict-strategy local_wins
    python scripts/cerid-sync.py status                          # compare local vs sync
"""

import argparse
import os
import socket
import sys

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}  [ok]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}  [warn]{RESET} {msg}")


def err(msg: str) -> None:
    print(f"{RED}  [error]{RESET} {msg}", file=sys.stderr)


def header(msg: str) -> None:
    print(f"\n{BOLD}{msg}{RESET}")


# ---------------------------------------------------------------------------
# Add src/mcp to sys.path so cerid_sync_lib can import config, utils, etc.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_MCP_SRC = os.path.join(_REPO_ROOT, "src", "mcp")
if _MCP_SRC not in sys.path:
    sys.path.insert(0, _MCP_SRC)

try:
    import cerid_sync_lib
except ImportError as _e:
    err(
        f"Cannot import cerid_sync_lib from {_MCP_SRC}: {_e}\n"
        "  Ensure src/mcp/cerid_sync_lib.py exists and dependencies are installed."
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Connection helpers — connect to containers via localhost (host-side ports)
# ---------------------------------------------------------------------------

CHROMA_HOST_URL = "http://localhost:8001"


def connect_neo4j(password: str):
    """Return a neo4j.Driver connected to bolt://localhost:7687."""
    import neo4j
    uri = "bolt://localhost:7687"
    driver = neo4j.GraphDatabase.driver(uri, auth=("neo4j", password))
    try:
        with driver.session() as session:
            session.run("RETURN 1")
    except Exception as exc:
        raise ConnectionError(f"Neo4j unreachable at {uri}: {exc}") from exc
    return driver


def connect_redis():
    """Return a redis.Redis client connected to redis://localhost:6379."""
    import redis
    url = "redis://localhost:6379"
    client = redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
    try:
        client.ping()
    except Exception as exc:
        raise ConnectionError(f"Redis unreachable at {url}: {exc}") from exc
    return client


def check_chroma():
    """Verify ChromaDB is reachable at CHROMA_HOST_URL."""
    import httpx
    try:
        resp = httpx.get(f"{CHROMA_HOST_URL}/api/v1/heartbeat", timeout=10.0)
        resp.raise_for_status()
    except Exception as exc:
        raise ConnectionError(f"ChromaDB unreachable at {CHROMA_HOST_URL}: {exc}") from exc


# ---------------------------------------------------------------------------
# Subcommand: export
# ---------------------------------------------------------------------------

def cmd_export(
    sync_dir: str,
    machine_id: str,
    since: str | None = None,
    domains: list[str] | None = None,
) -> int:
    """Export knowledge-base data to sync_dir. Returns exit code."""
    # Auto-read last_exported_at from manifest for incremental default
    if since is None:
        try:
            manifest = cerid_sync_lib.read_manifest(sync_dir=sync_dir)
            since = manifest.get("last_exported_at")
        except (FileNotFoundError, ValueError):
            pass

    mode = "incremental" if since else "full"
    header(f"Exporting knowledge base to: {sync_dir}  [{mode}]")
    print(f"  machine_id : {machine_id}")
    if since:
        print(f"  since      : {since}")
    if domains:
        print(f"  domains    : {', '.join(domains)}")

    neo4j_password = os.getenv("NEO4J_PASSWORD", "")

    # --- connect ---
    header("Connecting to services...")
    try:
        neo4j_driver = connect_neo4j(neo4j_password)
        ok("Neo4j connected (bolt://localhost:7687)")
    except ConnectionError as exc:
        err(str(exc))
        return 1

    try:
        check_chroma()
        ok(f"ChromaDB connected ({CHROMA_HOST_URL})")
    except ConnectionError as exc:
        err(str(exc))
        neo4j_driver.close()
        return 1

    try:
        redis_client = connect_redis()
        ok("Redis connected (redis://localhost:6379)")
    except ConnectionError as exc:
        err(str(exc))
        neo4j_driver.close()
        return 1

    os.makedirs(sync_dir, exist_ok=True)

    exit_code = 0

    # --- Neo4j ---
    header("Exporting Neo4j artifacts...")
    artifact_ids = None
    try:
        result = cerid_sync_lib.export_neo4j(
            driver=neo4j_driver,
            sync_dir=sync_dir,
            since=since,
            domains=domains,
        )
        artifact_ids = result.get("artifact_ids")
        ok(f"Neo4j: {result.get('artifacts', 0)} artifacts, {result.get('domains', 0)} domains, {result.get('relationships', 0)} relationships")
    except Exception as exc:
        err(f"Neo4j export failed: {exc}")
        exit_code = 1

    # --- ChromaDB ---
    header("Exporting ChromaDB vectors...")
    try:
        result = cerid_sync_lib.export_chroma(
            chroma_url=CHROMA_HOST_URL,
            sync_dir=sync_dir,
            artifact_ids=artifact_ids,
            filter_domains=domains,
        )
        ok(f"ChromaDB: {result.get('total_chunks', 0)} chunks across {len(result.get('domains', {}))} domains")
    except Exception as exc:
        err(f"ChromaDB export failed: {exc}")
        exit_code = 1

    # --- Tombstones ---
    header("Exporting tombstones...")
    try:
        result = cerid_sync_lib.export_tombstones(sync_dir=sync_dir)
        ok(f"Tombstones: {result.get('tombstones_exported', 0)} entries ({result.get('new_entries', 0)} new, {result.get('purged_expired', 0)} purged)")
    except Exception as exc:
        err(f"Tombstone export failed: {exc}")
        exit_code = 1

    # --- BM25 ---
    header("Exporting BM25 indexes...")
    try:
        result = cerid_sync_lib.export_bm25(sync_dir=sync_dir)
        ok(f"BM25: {result.get('files_copied', 0)} corpus files, {result.get('total_chunks', 0)} chunks")
    except Exception as exc:
        err(f"BM25 export failed: {exc}")
        exit_code = 1

    # --- Redis audit log ---
    header("Exporting Redis audit log...")
    try:
        result = cerid_sync_lib.export_redis(
            redis_client=redis_client,
            sync_dir=sync_dir,
        )
        ok(f"Redis: {result.get('entries_exported', 0)} audit events")
    except Exception as exc:
        err(f"Redis export failed: {exc}")
        exit_code = 1

    # --- Manifest ---
    if exit_code == 0:
        header("Writing manifest...")
        try:
            cerid_sync_lib.write_manifest(
                sync_dir=sync_dir,
                machine_id=machine_id,
                is_incremental=since is not None,
            )
            ok(f"Manifest written to {sync_dir}/manifest.json")
        except Exception as exc:
            err(f"Manifest write failed: {exc}")
            exit_code = 1

    # --- Cleanup ---
    neo4j_driver.close()

    if exit_code == 0:
        ok("Export complete.")
    else:
        warn("Export completed with errors (see above).")

    return exit_code


# ---------------------------------------------------------------------------
# Subcommand: import
# ---------------------------------------------------------------------------

def cmd_import(
    sync_dir: str,
    machine_id: str,
    force: bool,
    conflict_strategy: str = "remote_wins",
) -> int:
    """Import knowledge-base data from sync_dir. Returns exit code."""
    mode_label = "force (overwrite)" if force else "non-destructive (merge)"
    header(f"Importing knowledge base from: {sync_dir}  [{mode_label}]")
    print(f"  machine_id : {machine_id}")
    print(f"  conflicts  : {conflict_strategy}")

    if not os.path.isdir(sync_dir):
        err(f"Sync directory not found: {sync_dir}")
        return 1

    # --- Read manifest first ---
    header("Reading manifest...")
    try:
        manifest = cerid_sync_lib.read_manifest(sync_dir=sync_dir)
        ok(
            f"Manifest found — exported by '{manifest.get('machine_id', 'unknown')}' "
            f"at {manifest.get('last_exported_at', manifest.get('timestamp', 'unknown'))}"
        )
    except Exception as exc:
        err(f"Cannot read manifest from {sync_dir}: {exc}")
        return 1

    neo4j_password = os.getenv("NEO4J_PASSWORD", "")

    # --- connect ---
    header("Connecting to services...")
    try:
        neo4j_driver = connect_neo4j(neo4j_password)
        ok("Neo4j connected (bolt://localhost:7687)")
    except ConnectionError as exc:
        err(str(exc))
        return 1

    try:
        check_chroma()
        ok(f"ChromaDB connected ({CHROMA_HOST_URL})")
    except ConnectionError as exc:
        err(str(exc))
        neo4j_driver.close()
        return 1

    try:
        redis_client = connect_redis()
        ok("Redis connected (redis://localhost:6379)")
    except ConnectionError as exc:
        err(str(exc))
        neo4j_driver.close()
        return 1

    exit_code = 0

    # --- Tombstones (before import, so deleted artifacts aren't re-imported) ---
    header("Applying tombstones...")
    try:
        last_sync_at = manifest.get("last_exported_at")
        tomb_result = cerid_sync_lib.apply_tombstones(
            driver=neo4j_driver,
            chroma_url=CHROMA_HOST_URL,
            sync_dir=sync_dir,
        )
        ok(f"Tombstones: {tomb_result.get('deleted', 0)} deleted, {tomb_result.get('skipped_own_machine', 0)} skipped (own machine)")
    except Exception as exc:
        err(f"Tombstone apply failed: {exc}")
        exit_code = 1
        last_sync_at = None

    # --- Neo4j ---
    header("Importing Neo4j artifacts...")
    try:
        result = cerid_sync_lib.import_neo4j(
            driver=neo4j_driver,
            sync_dir=sync_dir,
            force=force,
            conflict_strategy=conflict_strategy,
            last_sync_at=last_sync_at,
        )
        created = result.get("artifacts_created", 0)
        updated = result.get("artifacts_updated", 0)
        skipped = result.get("artifacts_skipped", 0)
        conflicts = result.get("artifacts_conflict", 0)
        msg = f"Neo4j: {created} created, {updated} updated, {skipped} skipped"
        if conflicts:
            msg += f", {conflicts} conflicts ({conflict_strategy})"
        ok(msg)
    except Exception as exc:
        err(f"Neo4j import failed: {exc}")
        exit_code = 1

    # --- ChromaDB ---
    header("Importing ChromaDB vectors...")
    try:
        result = cerid_sync_lib.import_chroma(
            chroma_url=CHROMA_HOST_URL,
            sync_dir=sync_dir,
            force=force,
        )
        ok(f"ChromaDB: {result.get('total_added', 0)} added, {result.get('total_skipped', 0)} skipped")
    except Exception as exc:
        err(f"ChromaDB import failed: {exc}")
        exit_code = 1

    # --- BM25 ---
    header("Importing BM25 indexes...")
    try:
        result = cerid_sync_lib.import_bm25(sync_dir=sync_dir)
        ok(f"BM25: {result.get('chunks_added', 0)} added, {result.get('chunks_skipped', 0)} skipped")
    except Exception as exc:
        err(f"BM25 import failed: {exc}")
        exit_code = 1

    # --- Redis audit log ---
    header("Importing Redis audit log...")
    try:
        result = cerid_sync_lib.import_redis(
            redis_client=redis_client,
            sync_dir=sync_dir,
        )
        ok(f"Redis: {result.get('entries_added', 0)} added, {result.get('entries_skipped', 0)} skipped")
    except Exception as exc:
        err(f"Redis import failed: {exc}")
        exit_code = 1

    # --- Cleanup ---
    neo4j_driver.close()

    if exit_code == 0:
        ok("Import complete.")
    else:
        warn("Import completed with errors (see above).")

    return exit_code


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------

def cmd_status(sync_dir: str, machine_id: str) -> int:
    """Compare local knowledge base vs sync_dir snapshot. Returns exit code."""
    header(f"Status: comparing local KB vs sync dir: {sync_dir}")
    print(f"  machine_id : {machine_id}")

    if not os.path.isdir(sync_dir):
        warn(f"Sync directory not found: {sync_dir}  (no export yet?)")
        return 0

    neo4j_password = os.getenv("NEO4J_PASSWORD", "")

    # --- connect ---
    header("Connecting to services...")
    try:
        neo4j_driver = connect_neo4j(neo4j_password)
        ok("Neo4j connected (bolt://localhost:7687)")
    except ConnectionError as exc:
        err(str(exc))
        return 1

    try:
        check_chroma()
        ok(f"ChromaDB connected ({CHROMA_HOST_URL})")
    except ConnectionError as exc:
        err(str(exc))
        neo4j_driver.close()
        return 1

    try:
        redis_client = connect_redis()
        ok("Redis connected (redis://localhost:6379)")
    except ConnectionError as exc:
        err(str(exc))
        neo4j_driver.close()
        return 1

    header("Comparing...")
    try:
        comparison = cerid_sync_lib.compare_status(
            driver=neo4j_driver,
            chroma_url=CHROMA_HOST_URL,
            redis_client=redis_client,
            sync_dir=sync_dir,
        )
    except Exception as exc:
        err(f"Status comparison failed: {exc}")
        neo4j_driver.close()
        return 1

    # --- Cleanup ---
    neo4j_driver.close()

    # --- Print comparison table ---
    local = comparison.get("local", {})
    sync = comparison.get("sync", {})
    diff = comparison.get("diff", {})

    header("Comparison table:")
    col_w = 22
    print(
        f"  {'Resource':<{col_w}} {'Local':>10} {'Sync':>10} {'Delta':>10}"
    )
    print("  " + "-" * (col_w + 34))

    rows = [
        ("Neo4j artifacts", "neo4j_artifacts"),
        ("Neo4j domains", "neo4j_domains"),
        ("Neo4j relationships", "neo4j_relationships"),
        ("Redis entries", "redis_entries"),
    ]

    all_equal = True
    for label, key in rows:
        l_val = local.get(key, 0)
        s_val = sync.get(key, 0)
        d_val = diff.get(key, 0)
        if d_val != 0:
            all_equal = False
        if d_val > 0:
            delta_str = f"{GREEN}+{d_val}{RESET}"
        elif d_val < 0:
            delta_str = f"{YELLOW}{d_val}{RESET}"
        else:
            delta_str = "0"
        print(f"  {label:<{col_w}} {l_val:>10} {s_val:>10} {delta_str:>10}")

    # Chroma per-domain
    local_chroma = local.get("chroma_chunks", {})
    sync_chroma = sync.get("chroma_chunks", {})
    diff_chroma = diff.get("chroma_chunks", {})
    for domain in sorted(set(list(local_chroma.keys()) + list(sync_chroma.keys()))):
        l_val = local_chroma.get(domain, 0)
        s_val = sync_chroma.get(domain, 0)
        d_val = diff_chroma.get(domain, l_val - s_val)
        if d_val != 0:
            all_equal = False
        if d_val > 0:
            delta_str = f"{GREEN}+{d_val}{RESET}"
        elif d_val < 0:
            delta_str = f"{YELLOW}{d_val}{RESET}"
        else:
            delta_str = "0"
        print(f"  {'Chroma: ' + domain:<{col_w}} {l_val:>10} {s_val:>10} {delta_str:>10}")

    print()
    if all_equal:
        ok("Local KB is in sync with snapshot.")
    else:
        warn("Local KB differs from snapshot. Run 'export' to update or 'import' to pull.")

    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cerid-sync",
        description="Cerid AI — Knowledge Base Sync CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        choices=["export", "import", "status"],
        help="Subcommand to run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="(import only) Overwrite local data instead of merging.",
    )
    parser.add_argument(
        "--sync-dir",
        dest="sync_dir",
        default=None,
        help=(
            "Path to the sync directory. "
            "Overrides CERID_SYNC_DIR env var. "
            "Default: ~/Dropbox/cerid-sync"
        ),
    )
    parser.add_argument(
        "--machine-id",
        dest="machine_id",
        default=None,
        help=(
            "Identifier for this machine in the sync manifest. "
            "Default: system hostname."
        ),
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "(export only) ISO-8601 timestamp for incremental export. "
            "Only artifacts updated after this time are exported. "
            "If omitted, reads last_exported_at from manifest (or does full export)."
        ),
    )
    parser.add_argument(
        "--domains",
        nargs="+",
        default=None,
        help=(
            "(export only) Limit export to specific domains. "
            "Example: --domains code finance"
        ),
    )
    parser.add_argument(
        "--conflict-strategy",
        dest="conflict_strategy",
        choices=["remote_wins", "local_wins", "keep_both", "manual_review"],
        default="remote_wins",
        help=(
            "(import only) How to resolve conflicts when the same artifact "
            "was modified on both machines. Default: remote_wins."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve sync_dir: CLI flag > env var > default
    if args.sync_dir:
        sync_dir = os.path.expanduser(args.sync_dir)
    else:
        sync_dir = os.path.expanduser(
            os.getenv("CERID_SYNC_DIR", "~/Dropbox/cerid-sync")
        )

    # Resolve machine_id: CLI flag > hostname
    machine_id: str = args.machine_id or socket.gethostname()

    # Load .env from repo root if available
    env_file = os.path.join(_REPO_ROOT, ".env")
    if os.path.exists(env_file):
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            # python-dotenv not installed; rely on shell env
            pass

    if args.command == "export":
        return cmd_export(
            sync_dir=sync_dir,
            machine_id=machine_id,
            since=args.since,
            domains=args.domains,
        )
    elif args.command == "import":
        return cmd_import(
            sync_dir=sync_dir,
            machine_id=machine_id,
            force=args.force,
            conflict_strategy=args.conflict_strategy,
        )
    elif args.command == "status":
        return cmd_status(sync_dir=sync_dir, machine_id=machine_id)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())