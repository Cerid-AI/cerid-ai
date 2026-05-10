# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Knowledge-pack manifest, registry, verifier, and install-state helpers.

A *knowledge pack* is an optional, post-install corpus that bulks up a
freshly-installed Cerid KB across user-selected domains. The repo ships
slim — only a registry of available packs (id, version, domain, sha256,
download URL) is committed. Pack archives themselves are fetched on
demand by ``app.services.knowledge_packs`` and ingested through the
existing ``app.services.ingestion`` pipeline (so dedup, quality scoring,
and entity backfill all work without special-casing).

This module is pure: it parses manifests, validates archives, and tracks
which packs are installed. It never touches Neo4j / chromadb / FastAPI
— the layer contract (``core/`` ↛ ``app/``) is enforced by import-linter
and is the reason this split exists.

Pack archive shape (``tar.gz``)::

    pack.json          # PackManifest, JSON; same shape as registry entry
                       #  + optional "files" list with per-file overrides
    content/           # files to ingest (any layout; recursive)
        intro.md
        chapter-01.md
        ...

Registry shape (``config/knowledge_packs.json``)::

    {
      "schema_version": 1,
      "packs": [
        {
          "id": "cerid-starter-general",
          "name": "Cerid Starter — General Reference",
          "version": "1.0.0",
          "description": "Public-domain reference articles ...",
          "domain": "general",
          "sub_category": "reference",
          "tags": ["reference", "starter"],
          "license": "CC0-1.0",
          "size_bytes": 1234567,
          "artifact_count": 25,
          "download_url": "https://.../cerid-starter-general-1.0.0.tar.gz",
          "sha256": "abcd...",
          "provenance": {"source": "...", "curator": "Cerid AI"}
        }
      ]
    }

Install state shape (``.cerid-state/installed_packs.json``)::

    {
      "schema_version": 1,
      "packs": [
        {
          "pack_id": "cerid-starter-general",
          "version": "1.0.0",
          "installed_at": "2026-05-10T12:00:00+00:00",
          "domain": "general",
          "sha256": "abcd...",
          "artifact_ids": ["uuid1", "uuid2", ...]
        }
      ]
    }
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

REGISTRY_SCHEMA_VERSION = 1
INSTALL_STATE_SCHEMA_VERSION = 1

# Pack ids are url-safe slugs: lowercase + digits + hyphens, no leading/trailing
# hyphen. Keeps them safe as filenames, URL paths, and JSON keys.
_PACK_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class PackError(ValueError):
    """Raised when a manifest, registry, or archive fails validation."""


@dataclass(frozen=True)
class FileOverride:
    """Per-file override discovered in pack.json after extraction.

    Lets one pack tag certain files into a specific sub-category or
    apply file-level tags without splitting the pack. ``path`` is
    relative to the archive root (e.g. ``content/python/stdlib.md``).
    """

    path: str
    sub_category: str = ""
    tags: tuple[str, ...] = ()
    domain: str = ""  # rare: per-file domain override

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FileOverride":
        path = str(raw.get("path", "")).strip()
        if not path:
            raise PackError("FileOverride.path is required")
        return cls(
            path=path,
            sub_category=str(raw.get("sub_category", "")),
            tags=tuple(str(t) for t in raw.get("tags", ())),
            domain=str(raw.get("domain", "")),
        )


@dataclass(frozen=True)
class BuildSpec:
    """Adapter wiring for materialising a pack tarball from upstream.

    Lives on ``PackManifest.build`` (optional). The shipped registry
    keeps this empty for ``planned`` entries — a curator-build (Phase 7)
    populates it before fetching. The two-field shape (``adapter`` +
    ``config``) lets us add concrete adapters (``github_zip``,
    ``hf_dataset``, ``wikipedia_export``, ``gov_html_scrape``,
    ``gutenberg``, ``wikipedia_dump``) without re-versioning the
    registry schema.
    """

    adapter: str
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BuildSpec":
        adapter = str(raw.get("adapter", "")).strip()
        if not adapter:
            raise PackError("BuildSpec.adapter is required")
        config_raw = raw.get("config") or {}
        if not isinstance(config_raw, Mapping):
            raise PackError("BuildSpec.config must be an object")
        return cls(adapter=adapter, config=dict(config_raw))

    def to_dict(self) -> dict[str, Any]:
        return {"adapter": self.adapter, "config": dict(self.config)}


@dataclass(frozen=True)
class PackManifest:
    """Slim, JSON-serialisable metadata for a knowledge pack."""

    id: str
    name: str
    version: str
    description: str
    domain: str
    sub_category: str = ""
    tags: tuple[str, ...] = ()
    license: str = ""
    size_bytes: int = 0
    artifact_count: int = 0
    download_url: str = ""
    sha256: str = ""
    provenance: dict[str, str] = field(default_factory=dict)
    files: tuple[FileOverride, ...] = ()
    build: BuildSpec | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PackManifest":
        pack_id = str(raw.get("id", "")).strip()
        if not _PACK_ID_RE.match(pack_id):
            raise PackError(
                f"Invalid pack id {pack_id!r}: must match {_PACK_ID_RE.pattern}"
            )
        version = str(raw.get("version", "")).strip()
        if not version:
            raise PackError(f"Pack {pack_id!r} missing version")
        domain = str(raw.get("domain", "")).strip()
        if not domain:
            raise PackError(f"Pack {pack_id!r} missing domain")
        sha256 = str(raw.get("sha256", "")).strip().lower()
        if sha256 and not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise PackError(
                f"Pack {pack_id!r} sha256 must be 64-char hex, got {sha256!r}"
            )
        files_raw = raw.get("files") or ()
        if not isinstance(files_raw, (list, tuple)):
            raise PackError(f"Pack {pack_id!r}: 'files' must be a list")
        provenance_raw = raw.get("provenance") or {}
        if not isinstance(provenance_raw, Mapping):
            raise PackError(f"Pack {pack_id!r}: 'provenance' must be an object")
        build_raw = raw.get("build")
        build_spec: BuildSpec | None = None
        if build_raw is not None:
            if not isinstance(build_raw, Mapping):
                raise PackError(f"Pack {pack_id!r}: 'build' must be an object")
            build_spec = BuildSpec.from_dict(build_raw)
        return cls(
            id=pack_id,
            name=str(raw.get("name") or pack_id),
            version=version,
            description=str(raw.get("description", "")),
            domain=domain,
            sub_category=str(raw.get("sub_category", "")),
            tags=tuple(str(t) for t in raw.get("tags", ())),
            license=str(raw.get("license", "")),
            size_bytes=int(raw.get("size_bytes") or 0),
            artifact_count=int(raw.get("artifact_count") or 0),
            download_url=str(raw.get("download_url", "")),
            sha256=sha256,
            provenance={str(k): str(v) for k, v in provenance_raw.items()},
            files=tuple(FileOverride.from_dict(f) for f in files_raw),
            build=build_spec,
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["tags"] = list(self.tags)
        out["files"] = [asdict(f) for f in self.files]
        if self.build is None:
            out.pop("build", None)
        else:
            out["build"] = self.build.to_dict()
        return out

    @property
    def license_category(self) -> str:
        """SPDX-derived install-policy category. See :func:`license_category`."""
        return license_category(self.license)

    def is_buildable(self) -> bool:
        """``True`` iff this pack has been materialised (has a download_url).

        ``planned`` catalog entries ship in the registry with empty
        ``download_url`` so the Library UI can surface them, but
        ``install_pack`` will refuse them with a clear message until
        a curator publishes the tarball.
        """
        return bool(self.download_url)

    @property
    def status(self) -> str:
        """Coarse lifecycle state derived from manifest fields.

        - ``planned`` — registry entry exists, no tarball published yet
          (empty ``download_url``).
        - ``experimental`` — flagged in ``provenance.status`` for
          caveats (license edge case, content review pending).
        - ``built`` — fully materialised, install-able.
        """
        explicit = str(self.provenance.get("status", "")).strip().lower()
        if explicit == "experimental":
            return "experimental"
        if not self.download_url:
            return "planned"
        return "built"


# SPDX-style license categorisation. The harness uses these to decide
# whether to install a pack without prompting (public_domain / permissive
# / attribution) or to require an explicit ``--allow-share-alike``
# (share_alike). Anything not in this map is treated as ``unknown`` and
# the install path refuses it. The mapping is also persisted in
# ``config/knowledge_packs_allowlist.json`` for curator-build validation;
# the lists are kept in sync because two-source-of-truth for this would
# silently drift.
_LICENSE_CATEGORY: dict[str, str] = {
    # public domain
    "CC0-1.0": "public_domain",
    "Unlicense": "public_domain",
    "0BSD": "public_domain",
    "WTFPL": "public_domain",
    "us-gov-pd": "public_domain",
    # permissive (no share-alike, attribution often nominal)
    "MIT": "permissive",
    "Apache-2.0": "permissive",
    "BSD-2-Clause": "permissive",
    "BSD-3-Clause": "permissive",
    "BSL-1.0": "permissive",
    "ISC": "permissive",
    "PSF-2.0": "permissive",
    "Python-2.0": "permissive",
    "Blue-Oak-1.0.0": "permissive",
    "Zlib": "permissive",
    # attribution required, no share-alike
    "CC-BY-2.5": "attribution",
    "CC-BY-3.0": "attribution",
    "CC-BY-4.0": "attribution",
    "ODC-BY-1.0": "attribution",  # Open Data Commons Attribution
    "ODbL-1.0": "share_alike",   # Open Data Commons Open Database (share-alike-equivalent)
    # share-alike (derivatives, including embeddings, must propagate
    # the same license — flagged at install time so the operator can
    # acknowledge the obligation)
    "CC-BY-SA-2.5": "share_alike",
    "CC-BY-SA-3.0": "share_alike",
    "CC-BY-SA-4.0": "share_alike",
    "GFDL-1.3": "share_alike",
    "GPL-3.0-or-later": "share_alike",
}

# Categories that install_pack accepts by default. ``share_alike`` is
# omitted: the operator must opt in explicitly (CLI ``--allow-share-alike``
# or the equivalent UI toggle) so RAG-output republication obligations
# aren't accepted silently.
DEFAULT_INSTALL_CATEGORIES: frozenset[str] = frozenset(
    {"public_domain", "permissive", "attribution"}
)


def license_category(license_id: str) -> str:
    """Return the install-policy category for an SPDX license.

    Returns one of ``public_domain``, ``permissive``, ``attribution``,
    ``share_alike``, or ``unknown``. Unknown licenses are rejected at
    install time — a curator must either pin a recognized SPDX
    identifier or extend ``_LICENSE_CATEGORY`` after legal review.
    """
    return _LICENSE_CATEGORY.get(license_id.strip(), "unknown")


def validate_against_allowlist(
    pack: PackManifest, host_prefixes: Iterable[str],
) -> None:
    """Refuse a pack whose source URLs aren't in the curator allow-list.

    Hardens against typo-squatted upstreams and supply-chain hijacks
    (cf. Internetware-2025 study finding 625 typo-squatted HF datasets,
    42% with malicious intent). Both ``download_url`` (if set) *and*
    ``provenance['source']`` must match a host_prefix.

    Empty ``download_url`` is permitted — that's how the registry
    represents ``planned`` catalog entries that haven't been built yet.
    The ``provenance['source']`` check still runs because the catalog
    entry already commits to an upstream.
    """
    prefixes = list(host_prefixes)
    source_url = str(pack.provenance.get("source", "")).strip()
    if not source_url:
        raise PackError(
            f"Pack {pack.id!r}: provenance.source is required for allow-list validation",
        )
    if not any(source_url.startswith(p) for p in prefixes):
        raise PackError(
            f"Pack {pack.id!r}: provenance.source {source_url!r} is not in the "
            f"upstream allow-list. Add an entry to "
            f"config/knowledge_packs_allowlist.json after vetting the upstream.",
        )
    if pack.download_url and not any(pack.download_url.startswith(p) for p in prefixes):
        # ``file://`` URLs are tolerated for local-build registries
        # (curators iterating before publishing) but still must not
        # masquerade as a remote https origin.
        if not pack.download_url.startswith("file://"):
            raise PackError(
                f"Pack {pack.id!r}: download_url {pack.download_url!r} is not "
                f"in the upstream allow-list.",
            )


def load_allowlist(path: str | Path) -> dict[str, Any]:
    """Read ``config/knowledge_packs_allowlist.json``. Returns ``{}`` if absent."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackError(f"Allow-list at {p} is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PackError(f"Allow-list at {p} top-level must be an object")
    return dict(data)


@dataclass(frozen=True)
class InstalledPack:
    """Record of a pack that has been ingested into the local KB."""

    pack_id: str
    version: str
    installed_at: str
    domain: str
    sha256: str
    artifact_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "InstalledPack":
        return cls(
            pack_id=str(raw["pack_id"]),
            version=str(raw["version"]),
            installed_at=str(raw["installed_at"]),
            domain=str(raw.get("domain", "")),
            sha256=str(raw.get("sha256", "")),
            artifact_ids=tuple(str(x) for x in raw.get("artifact_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "installed_at": self.installed_at,
            "domain": self.domain,
            "sha256": self.sha256,
            "artifact_ids": list(self.artifact_ids),
        }


# ── Registry I/O ────────────────────────────────────────────────────────────

def parse_registry(blob: str | bytes) -> dict[str, PackManifest]:
    """Parse a registry JSON blob into a ``{pack_id: PackManifest}`` map.

    Validates ``schema_version`` and that no two packs share an id.
    """
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise PackError(f"Registry is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PackError("Registry top-level must be an object")
    schema = data.get("schema_version")
    if schema != REGISTRY_SCHEMA_VERSION:
        raise PackError(
            f"Unsupported registry schema_version {schema!r}; "
            f"expected {REGISTRY_SCHEMA_VERSION}"
        )
    packs_raw = data.get("packs") or []
    if not isinstance(packs_raw, list):
        raise PackError("Registry 'packs' must be a list")
    out: dict[str, PackManifest] = {}
    for entry in packs_raw:
        if not isinstance(entry, Mapping):
            raise PackError("Registry pack entries must be objects")
        pack = PackManifest.from_dict(entry)
        if pack.id in out:
            raise PackError(f"Duplicate pack id in registry: {pack.id!r}")
        out[pack.id] = pack
    return out


def load_registry(path: str | Path) -> dict[str, PackManifest]:
    """Load + parse a registry JSON file. Returns ``{}`` if the file is absent."""
    p = Path(path)
    if not p.exists():
        return {}
    return parse_registry(p.read_text(encoding="utf-8"))


def serialise_registry(packs: Iterable[PackManifest]) -> str:
    """Serialise a registry back to JSON (round-trip / curator tool)."""
    return json.dumps(
        {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "packs": [p.to_dict() for p in packs],
        },
        indent=2,
        sort_keys=False,
    )


# ── Pack archive verification ─────────────────────────────────────────────

_HASH_CHUNK_BYTES = 1 << 20  # 1 MiB; balances syscalls vs memory


def sha256_of_file(path: str | Path) -> str:
    """Stream-hash a file, return its lowercase hex sha256 digest."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_archive_sha256(path: str | Path, expected: str) -> None:
    """Raise :class:`PackError` if the file's sha256 doesn't match ``expected``.

    ``expected`` may be empty — in which case verification is skipped
    (the curator chose to publish without a sum). Always log the
    actual digest so an operator can pin it later.
    """
    if not expected:
        return
    actual = sha256_of_file(path)
    if actual.lower() != expected.lower():
        raise PackError(
            f"Archive sha256 mismatch: expected {expected}, got {actual}"
        )


def parse_pack_json(blob: str | bytes) -> PackManifest:
    """Parse the ``pack.json`` extracted from inside a pack archive."""
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise PackError(f"pack.json is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PackError("pack.json top-level must be an object")
    return PackManifest.from_dict(data)


def assert_archive_path_safe(member_name: str, *, archive_root: str = "") -> None:
    """Reject tarball members that try to escape the extraction root.

    Tarballs are a notorious vector for path-traversal attacks (the
    classic ``../../etc/passwd`` member). Call this on every archive
    member name *before* extraction. ``archive_root`` is optional and
    used only for error context.
    """
    if not member_name or member_name.startswith("/"):
        raise PackError(
            f"Unsafe archive member {member_name!r} in {archive_root or 'pack'}"
        )
    parts = Path(member_name).parts
    if any(p == ".." for p in parts):
        raise PackError(
            f"Path-traversal archive member {member_name!r} in {archive_root or 'pack'}"
        )


# ── Install-state tracking ────────────────────────────────────────────────

def parse_install_state(blob: str | bytes) -> list[InstalledPack]:
    """Parse install-state JSON. Empty + missing-schema treated as empty."""
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8")
    if not blob.strip():
        return []
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise PackError(f"Install state is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PackError("Install state top-level must be an object")
    schema = data.get("schema_version")
    if schema not in (None, INSTALL_STATE_SCHEMA_VERSION):
        raise PackError(
            f"Unsupported install-state schema_version {schema!r}; "
            f"expected {INSTALL_STATE_SCHEMA_VERSION}"
        )
    packs_raw = data.get("packs") or []
    if not isinstance(packs_raw, list):
        raise PackError("Install state 'packs' must be a list")
    return [InstalledPack.from_dict(p) for p in packs_raw]


def load_install_state(path: str | Path) -> list[InstalledPack]:
    """Load + parse install state. Returns ``[]`` if the file is absent."""
    p = Path(path)
    if not p.exists():
        return []
    return parse_install_state(p.read_text(encoding="utf-8"))


def serialise_install_state(packs: Iterable[InstalledPack]) -> str:
    return json.dumps(
        {
            "schema_version": INSTALL_STATE_SCHEMA_VERSION,
            "packs": [p.to_dict() for p in packs],
        },
        indent=2,
        sort_keys=False,
    )


def save_install_state(path: str | Path, packs: Iterable[InstalledPack]) -> None:
    """Atomic-write the install-state file: write to ``.tmp``, then rename.

    Atomicity matters because a partial write would leave the operator
    with a corrupt registry whose only recovery is "rerun a long install".
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(serialise_install_state(packs), encoding="utf-8")
    tmp.replace(p)


def find_installed(
    state: Iterable[InstalledPack], pack_id: str, *, version: str | None = None,
) -> InstalledPack | None:
    """Locate an installed pack by id (and optional exact version match)."""
    for pack in state:
        if pack.pack_id == pack_id and (version is None or pack.version == version):
            return pack
    return None


def upsert_installed(
    state: Iterable[InstalledPack], record: InstalledPack,
) -> list[InstalledPack]:
    """Return a new list with ``record`` replacing any prior entry for its id."""
    out = [p for p in state if p.pack_id != record.pack_id]
    out.append(record)
    return out


def remove_installed(
    state: Iterable[InstalledPack], pack_id: str,
) -> list[InstalledPack]:
    """Return a new list with the record for ``pack_id`` removed."""
    return [p for p in state if p.pack_id != pack_id]
