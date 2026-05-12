# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Vault source-type profile (Workstream RAG Cycle C2.3).

A "vault" is a markdown folder structured like an Obsidian / Logseq
vault — distinct sub-folders carry distinct semantics (Maps of Content,
daily notes, templates, attachments).  The folder scanner asks this
module to classify each file path before deciding how (or whether) to
ingest it.

Configuration sources, in precedence order:

1. ``.cerid-vault.yaml`` in the vault root — the canonical, repo-checked
   shape of the vault.  Authored by the user; survives moves of the
   vault between machines.
2. UI form values provided at watched-folder registration time — the
   fallback when no YAML file is present.
3. ``DEFAULT_VAULT_CONFIG`` — sensible defaults so a brand-new vault
   "just works" without any configuration.

YAML wins on key conflicts.  Defaults fill any unspecified keys.

Lives in ``core/`` so the scanner (in ``app/``) can call it without
crossing the import-linter ``core ↛ app`` boundary.  Pure Python —
no FastAPI / Redis / store-driver dependencies.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.ingest.vault_config")

__all__ = [
    "DEFAULT_VAULT_CONFIG",
    "PathClassification",
    "VaultProfile",
    "VAULT_CONFIG_FILENAME",
    "build_profile",
    "load_vault_yaml",
]

# Filename the scanner probes for at the vault root.
VAULT_CONFIG_FILENAME = ".cerid-vault.yaml"

# Defaults are tuned to match Obsidian conventions while being case-insensitive
# at classification time (see ``_normalise_folder_names``).
DEFAULT_VAULT_CONFIG: dict[str, Any] = {
    "mocs_folders": ["mocs", "MOCs", "maps-of-content"],
    "daily_folders": ["daily", "journal", "daily-notes"],
    "templates_folders": ["templates", "_templates", ".templates"],
    "attachments_folders": ["attachments", "_attachments", "assets"],
    # ``skip_folders`` is always added on top of ``templates_folders``;
    # these are folders that should never produce a single ingestion.
    "skip_folders": [".obsidian", ".trash", ".git"],
    "default_domain": "general",
}

# Keys we accept from a user-supplied config (UI form or YAML).  Anything
# else is silently dropped — vault YAML is user-authored, and we'd rather
# miss a typo'd key than commit arbitrary user input as profile state.
_ALLOWED_KEYS: frozenset[str] = frozenset(DEFAULT_VAULT_CONFIG.keys())

# Keys that hold folder-name lists (everything except ``default_domain``).
_LIST_KEYS: frozenset[str] = frozenset(
    k for k, v in DEFAULT_VAULT_CONFIG.items() if isinstance(v, list)
)


class PathClassification(str, Enum):
    """How a file in a vault should be handled."""

    SKIP = "skip"
    MOC = "moc"
    DAILY = "daily"
    ATTACHMENT = "attachment"
    REGULAR = "regular"


@dataclass(frozen=True, slots=True)
class VaultProfile:
    """Immutable classification policy for a single vault root.

    ``classify_path`` is the only method the scanner needs — given a
    path relative to ``root_path`` it returns a ``PathClassification``
    which the scanner uses to either skip the file, tag it with a
    ``sub_category``, or route it through the binary-attachment pipeline.
    """

    root_path: str
    mocs_folders: tuple[str, ...]
    daily_folders: tuple[str, ...]
    templates_folders: tuple[str, ...]
    attachments_folders: tuple[str, ...]
    skip_folders: tuple[str, ...]
    default_domain: str

    def classify_path(self, rel_path: str) -> PathClassification:
        """Classify a path relative to ``root_path``.

        The classification considers only the first path component
        (the top-level folder under the vault root) — matching is
        case-insensitive.  Empty/dotted/absolute paths return
        ``REGULAR`` so the caller can decide what to do with them.

        Order of checks:

        * ``skip_folders`` and ``templates_folders`` → ``SKIP``
        * ``mocs_folders``                          → ``MOC``
        * ``daily_folders``                         → ``DAILY``
        * ``attachments_folders``                   → ``ATTACHMENT``
        * else                                      → ``REGULAR``
        """
        if not rel_path:
            return PathClassification.REGULAR

        # Normalise both forward- and back-slashes so the classifier
        # behaves the same on POSIX and Windows scans.  ``os.sep`` would
        # be wrong here — the scanner emits POSIX-style relpaths in
        # tests even on macOS, and we want stable behaviour everywhere.
        #
        # Strip a single leading ``./`` (relative-path marker) and any
        # leading ``/`` but NEVER use ``lstrip("./")`` — that strips
        # any combination of those characters and would eat the leading
        # dot of ``.obsidian`` / ``.trash``.
        normalised = rel_path.replace("\\", "/")
        if normalised.startswith("./"):
            normalised = normalised[2:]
        normalised = normalised.lstrip("/")
        if not normalised:
            return PathClassification.REGULAR

        first = normalised.split("/", 1)[0].lower()
        if not first:
            return PathClassification.REGULAR

        # Skip wins over everything else — a folder that's both a
        # "template" and listed under skip stays skipped.
        if first in self.skip_folders or first in self.templates_folders:
            return PathClassification.SKIP
        if first in self.mocs_folders:
            return PathClassification.MOC
        if first in self.daily_folders:
            return PathClassification.DAILY
        if first in self.attachments_folders:
            return PathClassification.ATTACHMENT
        return PathClassification.REGULAR


def _normalise_folder_names(value: Any) -> tuple[str, ...]:
    """Coerce a YAML/UI folder-name list into a lower-cased tuple.

    Accepts a string (single folder), a list of strings, or anything
    coercible to ``str``.  Empty strings and non-string items in lists
    are dropped.  Comparison at classification time is case-insensitive,
    so we lower-case once here and never again.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        s = value.strip()
        return (s.lower(),) if s else ()
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if s:
                out.append(s.lower())
        return tuple(out)
    return ()


def load_vault_yaml(vault_root: str) -> dict[str, Any] | None:
    """Load ``.cerid-vault.yaml`` from the vault root.

    Returns ``None`` if the file is missing.  Returns ``{}`` if the file
    is empty or malformed (so callers can distinguish "no file" from
    "file present but provides no overrides").  Never raises — the
    scanner runs in a background task and must not crash on a typo'd
    YAML file.
    """
    if not vault_root:
        return None
    config_path = os.path.join(vault_root, VAULT_CONFIG_FILENAME)
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        logger.debug("vault_config.yaml_parse_failed: %s", e)
        return {}
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.ingest.vault_config.load_yaml", e)
        return {}

    if not isinstance(data, dict):
        # A YAML scalar/list at the top level is meaningless for us —
        # treat it as if the file were empty so callers fall through
        # to UI config / defaults.
        return {}
    return data


def _merged_config(
    yaml_config: dict[str, Any] | None,
    ui_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Layer YAML over UI over defaults.

    Only allowlisted keys flow through.  YAML wins on any key it sets;
    UI config fills keys YAML omits; defaults fill anything still
    missing.  An *explicitly empty list* from YAML is respected — the
    user can disable a category by writing ``mocs_folders: []``.
    """
    merged: dict[str, Any] = dict(DEFAULT_VAULT_CONFIG)

    if ui_config:
        for k, v in ui_config.items():
            if k in _ALLOWED_KEYS:
                merged[k] = v

    if yaml_config:
        for k, v in yaml_config.items():
            if k in _ALLOWED_KEYS:
                merged[k] = v

    return merged


def build_profile(
    vault_root: str,
    ui_config: dict[str, Any] | None = None,
) -> VaultProfile:
    """Build a ``VaultProfile`` for ``vault_root``.

    Loads ``.cerid-vault.yaml`` (if present) and layers it over the
    optional ``ui_config`` from the settings form.  Missing keys fall
    back to ``DEFAULT_VAULT_CONFIG``.

    ``skip_folders`` is always unioned with ``templates_folders`` at
    classification time (via ``classify_path``), but the profile keeps
    them as separate tuples so callers can inspect the user's intent.
    """
    yaml_cfg = load_vault_yaml(vault_root)
    merged = _merged_config(yaml_cfg, ui_config)

    default_domain_raw = merged.get("default_domain", DEFAULT_VAULT_CONFIG["default_domain"])
    default_domain = (
        default_domain_raw.strip()
        if isinstance(default_domain_raw, str) and default_domain_raw.strip()
        else DEFAULT_VAULT_CONFIG["default_domain"]
    )

    return VaultProfile(
        root_path=str(Path(vault_root)) if vault_root else "",
        mocs_folders=_normalise_folder_names(merged.get("mocs_folders")),
        daily_folders=_normalise_folder_names(merged.get("daily_folders")),
        templates_folders=_normalise_folder_names(merged.get("templates_folders")),
        attachments_folders=_normalise_folder_names(merged.get("attachments_folders")),
        skip_folders=_normalise_folder_names(merged.get("skip_folders")),
        default_domain=default_domain,
    )


def profile_to_dict(profile: VaultProfile) -> dict[str, Any]:
    """Serialise a ``VaultProfile`` for an HTTP response.

    Lists are returned as ``list[str]`` (not tuples) so the JSON shape
    is what frontend clients expect.
    """
    return {
        "root_path": profile.root_path,
        "mocs_folders": list(profile.mocs_folders),
        "daily_folders": list(profile.daily_folders),
        "templates_folders": list(profile.templates_folders),
        "attachments_folders": list(profile.attachments_folders),
        "skip_folders": list(profile.skip_folders),
        "default_domain": profile.default_domain,
    }
