# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A dependency nobody watches is a dependency that rots silently.

Two invariants, both about CURRENCY rather than correctness:

COVERAGE
    Every directory holding a manifest Dependabot understands — a Dockerfile,
    a docker-compose file, a package.json, a requirements.txt — must appear as
    an entry in ``.github/dependabot.yml`` under the ecosystem that actually
    reads that manifest. An uncovered directory never produces an update PR,
    so its pins age without anything reporting.

    The ecosystem matters, not just the directory. ``docker`` reads Dockerfiles
    (and Kubernetes manifests); compose files belong to ``docker-compose``. The
    first version of this gate conflated the two, and every compose-only entry
    it demanded then aborted on each scheduled run with "No Dockerfiles nor
    Kubernetes YAML found" — coverage on paper, nothing watching in practice.
    A compose file with no ``image:`` key (every service built from source)
    has nothing any updater can bump and is not required to be covered.

    This was not hypothetical when the gate was written on 2026-08-31. The
    ROOT ``docker-compose.yml`` — neo4j, redis, chroma, the core stack the
    live-stack gates boot — was watched by nothing at all, along with
    ``stacks/connectors`` and ``stacks/langfuse``. Two package directories
    were in the same state until 2026-08-28 and had accumulated 52 advisories
    between them.

PINNING
    No image may float. ``:latest`` and a bare repository with no tag both
    mean "whatever the registry served today", which makes a CI run
    unreproducible and a rollback impossible: there is no version to go back
    to. Floating references are allowlisted individually and the allowlist may
    only shrink.

Usage:
    python scripts/lint-dependency-currency.py            # check, non-zero on failure
    python scripts/lint-dependency-currency.py --list     # show what is covered
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEPENDABOT = REPO / ".github" / "dependabot.yml"

#: Paths that are not this repository's dependencies to keep current.
SKIP_PARTS = {".git", "node_modules", ".worktrees", ".venv", "dist", "build", "release"}

#: Floating image references that are deliberately not pinned. Each needs a
#: reason. This list may only SHRINK — adding to it is admitting a new place
#: where a rebuild can silently change what runs.
FLOATING_ALLOWLIST = {
    # Operator-overridable by design: the default only applies when the
    # deployer has not chosen a model runtime, and pinning it here would pin
    # THEIR runtime, not ours.
    "${CERID_OLLAMA_IMAGE:-ollama/ollama:latest}",
}

_IMAGE_RE = re.compile(r"^\s*image:\s*[\"']?([^\"'\s]+)", re.M)
_FROM_RE = re.compile(r"^\s*FROM\s+(?!--)([^\s]+)", re.M | re.I)


def _skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def _dependabot_dirs() -> dict[str, set[str]]:
    cfg = yaml.safe_load(DEPENDABOT.read_text())
    out: dict[str, set[str]] = {}
    for u in cfg.get("updates", []):
        eco = u["package-ecosystem"]
        for d in [u["directory"]] if "directory" in u else u.get("directories", []):
            out.setdefault(eco, set()).add(d.rstrip("/") or "/")
    return out


def _dir_of(p: Path) -> str:
    rel = p.parent.relative_to(REPO).as_posix()
    return "/" if rel == "." else f"/{rel}"


def _tracked() -> list[Path]:
    """Git-TRACKED files only.

    An earlier version walked the filesystem with rglob, which meant any
    untracked artifact could trip the gate: the public mirror carries an
    untracked docs/assets/demo-video/.../mockups/package.json, and the gate
    would have demanded dependabot coverage for a directory that is not even
    in the repository. What this gate is about is the dependencies the REPO
    declares, so ask git rather than the disk.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [REPO / f for f in out.split("\0") if f]


def _discover() -> dict[str, set[str]]:
    """Directories that hold a manifest, keyed by the ecosystem Dependabot uses."""
    found: dict[str, set[str]] = {"docker": set(), "docker-compose": set(), "npm": set(), "pip": set()}
    matchers = (
        ("docker", lambda p: p.name.startswith("Dockerfile")),
        ("docker-compose", lambda p: _is_compose(p) and bool(_IMAGE_RE.search(p.read_text(errors="ignore")))),
        ("npm", lambda p: p.name == "package.json"),
        ("pip", lambda p: p.name == "requirements.txt"),
    )
    for p in _tracked():
        rel = p.relative_to(REPO)
        if _skip(rel):
            continue
        for eco, match in matchers:
            if match(p):
                found[eco].add(_dir_of(p))
    return found


def _is_compose(p: Path) -> bool:
    return p.name.startswith("docker-compose") and p.suffix in (".yml", ".yaml")


def _floating() -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for p in _tracked():
        if _skip(p.relative_to(REPO)):
            continue
        if _is_compose(p):
            regex = _IMAGE_RE
        elif p.name.startswith("Dockerfile"):
            regex = _FROM_RE
        else:
            continue
        for ref in regex.findall(p.read_text(errors="ignore")):
            if ref in FLOATING_ALLOWLIST or ref.startswith("$"):
                continue
            # a multi-stage alias (`FROM builder AS models`) is not an image
            if ":" not in ref and "/" not in ref:
                continue
            if ref.endswith(":latest") or ":" not in ref.rsplit("/", 1)[-1]:
                hits.append((p.relative_to(REPO).as_posix(), ref))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    watched, found = _dependabot_dirs(), _discover()
    if args.list:
        for eco in sorted(found):
            print(f"{eco}:")
            for d in sorted(found[eco]):
                print(f"  {'OK  ' if d in watched.get(eco, set()) else 'GAP '} {d}")
        return 0

    errors: list[str] = []
    for eco in sorted(found):
        for d in sorted(found[eco] - watched.get(eco, set())):
            errors.append(
                f"{eco}: {d} holds a manifest but no dependabot.yml entry watches it — "
                f"its pins will age with nothing reporting"
            )
    for path, ref in sorted(_floating()):
        errors.append(f"floating image `{ref}` in {path} — pin it, or allowlist it with a reason")

    if errors:
        for e in errors:
            print(f"::error::[dependency-currency] {e}")
        print(f"[dependency-currency] {len(errors)} problem(s)")
        return 1

    n = sum(len(v) for v in found.values())
    print(
        f"[dependency-currency] OK — {n} manifest director{'y' if n == 1 else 'ies'} all watched by "
        f"dependabot; {len(FLOATING_ALLOWLIST)} allowlisted floating ref(s) (may only shrink)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
