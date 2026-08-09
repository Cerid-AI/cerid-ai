# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for ``core.knowledge.packs`` — pure manifest / registry /
verifier / install-state helpers. No network, no Docker, no LLM."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.knowledge.packs import (
    DEFAULT_INSTALL_CATEGORIES,
    INSTALL_STATE_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION,
    FileOverride,
    InstalledPack,
    PackError,
    PackManifest,
    assert_archive_path_safe,
    find_installed,
    license_category,
    load_allowlist,
    load_install_state,
    load_registry,
    parse_install_state,
    parse_pack_json,
    parse_registry,
    remove_installed,
    save_install_state,
    serialise_install_state,
    serialise_registry,
    sha256_of_file,
    upsert_installed,
    validate_against_allowlist,
    verify_archive_sha256,
)

# Reusable fixtures ---------------------------------------------------------

VALID_PACK = {
    "id": "cerid-starter-general",
    "name": "Cerid Starter — General Reference",
    "version": "1.0.0",
    "description": "Public-domain reference articles",
    "domain": "general",
    "sub_category": "reference",
    "tags": ["reference", "starter"],
    "license": "CC0-1.0",
    "size_bytes": 12345,
    "artifact_count": 25,
    "download_url": "https://example.org/cerid-starter-general-1.0.0.tar.gz",
    "sha256": "a" * 64,
    "provenance": {"source": "Public domain", "curator": "Cerid AI"},
    "files": [
        {"path": "content/intro.md", "sub_category": "general"},
        {
            "path": "content/python.md",
            "sub_category": "python",
            "tags": ["python", "reference"],
        },
    ],
}


# ── PackManifest.from_dict / to_dict ─────────────────────────────────────

def test_pack_manifest_from_dict_round_trips():
    pack = PackManifest.from_dict(VALID_PACK)
    assert pack.id == "cerid-starter-general"
    assert pack.domain == "general"
    assert len(pack.files) == 2
    assert pack.files[1].sub_category == "python"
    # Round-trip through to_dict + from_dict yields identical manifest.
    again = PackManifest.from_dict(pack.to_dict())
    assert again == pack


@pytest.mark.parametrize("bad_id", [
    "",                # empty
    "Has-Caps",        # uppercase
    "-leading-hyphen", # leading hyphen
    "trailing-",       # trailing hyphen
    "has spaces",      # whitespace
    "with/slash",      # path separator
    "x" * 65,          # too long
])
def test_pack_manifest_rejects_bad_id(bad_id):
    raw = {**VALID_PACK, "id": bad_id}
    with pytest.raises(PackError, match="Invalid pack id"):
        PackManifest.from_dict(raw)


def test_pack_manifest_rejects_missing_version():
    raw = {**VALID_PACK, "version": ""}
    with pytest.raises(PackError, match="missing version"):
        PackManifest.from_dict(raw)


def test_pack_manifest_rejects_missing_domain():
    raw = {**VALID_PACK, "domain": ""}
    with pytest.raises(PackError, match="missing domain"):
        PackManifest.from_dict(raw)


def test_pack_manifest_rejects_bad_sha256():
    raw = {**VALID_PACK, "sha256": "not-hex"}
    with pytest.raises(PackError, match="sha256 must be 64-char hex"):
        PackManifest.from_dict(raw)


def test_pack_manifest_accepts_empty_sha256():
    """Empty sha256 is allowed — curator may publish without a sum."""
    raw = {**VALID_PACK, "sha256": ""}
    pack = PackManifest.from_dict(raw)
    assert pack.sha256 == ""


def test_file_override_requires_path():
    with pytest.raises(PackError, match="path is required"):
        FileOverride.from_dict({"sub_category": "x"})


# ── Registry parsing / loading ──────────────────────────────────────────

def test_parse_registry_happy_path():
    blob = json.dumps({
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "packs": [VALID_PACK],
    })
    registry = parse_registry(blob)
    assert "cerid-starter-general" in registry
    assert registry["cerid-starter-general"].domain == "general"


def test_parse_registry_rejects_wrong_schema():
    blob = json.dumps({"schema_version": 999, "packs": []})
    with pytest.raises(PackError, match="schema_version"):
        parse_registry(blob)


def test_parse_registry_rejects_duplicate_ids():
    blob = json.dumps({
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "packs": [VALID_PACK, VALID_PACK],
    })
    with pytest.raises(PackError, match="Duplicate pack id"):
        parse_registry(blob)


def test_parse_registry_rejects_invalid_json():
    with pytest.raises(PackError, match="not valid JSON"):
        parse_registry("{ not json")


def test_load_registry_returns_empty_when_missing(tmp_path):
    assert load_registry(tmp_path / "nope.json") == {}


def test_load_registry_reads_real_file(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "packs": [VALID_PACK],
    }))
    registry = load_registry(path)
    assert list(registry.keys()) == ["cerid-starter-general"]


def test_serialise_registry_round_trip():
    pack = PackManifest.from_dict(VALID_PACK)
    blob = serialise_registry([pack])
    again = parse_registry(blob)
    assert again[pack.id] == pack


def test_repo_default_registry_is_valid_and_slim():
    """The committed default registry must parse cleanly and ship slim.

    The registry holds catalog metadata only — never bundled content —
    so its serialised form must stay tiny even when 14+ catalog
    entries are populated. The 50 KB cap leaves plenty of headroom
    while preventing accidental content commits.
    """
    mcp_root = Path(__file__).resolve().parents[1]
    registry_path = mcp_root / "config" / "knowledge_packs.json"
    assert registry_path.exists(), "registry file missing"
    assert registry_path.stat().st_size < 50 * 1024, "registry exceeded 50 KB"
    registry = load_registry(registry_path)
    assert isinstance(registry, dict)


def test_repo_default_registry_entries_are_well_formed():
    """Every shipped catalog entry must declare provenance + a known SPDX id.

    Catches the most common curator mistakes: forgotten provenance,
    typo'd license string, or planned packs missing the explicit
    status flag in provenance.
    """
    mcp_root = Path(__file__).resolve().parents[1]
    registry = load_registry(mcp_root / "config" / "knowledge_packs.json")
    for pack_id, pack in registry.items():
        assert pack.provenance.get("source"), (
            f"{pack_id}: provenance.source missing"
        )
        assert pack.license_category != "unknown", (
            f"{pack_id}: license {pack.license!r} not in SPDX category map"
        )
        # license_category in provenance, when set, must agree with derivation.
        prov_cat = pack.provenance.get("license_category")
        if prov_cat:
            assert prov_cat == pack.license_category, (
                f"{pack_id}: provenance license_category {prov_cat!r} "
                f"disagrees with SPDX derivation {pack.license_category!r}"
            )


# ── license_category + status helpers ──────────────────────────────────

@pytest.mark.parametrize("license_id, expected", [
    ("CC0-1.0", "public_domain"),
    ("Unlicense", "public_domain"),
    ("MIT", "permissive"),
    ("Apache-2.0", "permissive"),
    ("PSF-2.0", "permissive"),
    ("CC-BY-4.0", "attribution"),
    ("CC-BY-SA-3.0", "share_alike"),
    ("CC-BY-SA-4.0", "share_alike"),
    ("GFDL-1.3", "share_alike"),
    ("Proprietary", "unknown"),
    ("", "unknown"),
])
def test_license_category_mapping(license_id, expected):
    assert license_category(license_id) == expected


def test_pack_manifest_license_category_matches_spdx():
    pack = PackManifest.from_dict({**VALID_PACK, "license": "CC-BY-SA-4.0"})
    assert pack.license_category == "share_alike"


def test_pack_manifest_status_planned_when_no_download_url():
    raw = {**VALID_PACK, "download_url": ""}
    assert PackManifest.from_dict(raw).status == "planned"


def test_pack_manifest_status_built_when_download_url_present():
    pack = PackManifest.from_dict(VALID_PACK)
    assert pack.is_buildable()
    assert pack.status == "built"


def test_pack_manifest_status_experimental_overrides_built_status():
    raw = {
        **VALID_PACK,
        "provenance": {**VALID_PACK["provenance"], "status": "experimental"},
    }
    assert PackManifest.from_dict(raw).status == "experimental"


def test_default_install_categories_excludes_share_alike():
    """share_alike must NOT be in the default install set — operator must opt in."""
    assert "share_alike" not in DEFAULT_INSTALL_CATEGORIES
    assert "public_domain" in DEFAULT_INSTALL_CATEGORIES
    assert "permissive" in DEFAULT_INSTALL_CATEGORIES
    assert "attribution" in DEFAULT_INSTALL_CATEGORIES


# ── upstream allow-list ───────────────────────────────────────────────

def test_validate_against_allowlist_accepts_known_prefix():
    pack = PackManifest.from_dict({
        **VALID_PACK,
        "download_url": "https://github.com/mdn/content/releases/v1/pack.tar.gz",
        "provenance": {"source": "https://github.com/mdn/content"},
    })
    validate_against_allowlist(pack, ["https://github.com/mdn/"])


def test_validate_against_allowlist_rejects_typosquat():
    pack = PackManifest.from_dict({
        **VALID_PACK,
        "download_url": "https://github.com/wkimedia/content/releases/v1/pack.tar.gz",
        "provenance": {"source": "https://github.com/wkimedia/content"},
    })
    with pytest.raises(PackError, match="not in the upstream allow-list"):
        validate_against_allowlist(pack, ["https://github.com/wikimedia/"])


def test_validate_against_allowlist_rejects_missing_source():
    pack = PackManifest.from_dict({
        **VALID_PACK,
        "provenance": {},
    })
    with pytest.raises(PackError, match="provenance.source is required"):
        validate_against_allowlist(pack, ["https://github.com/mdn/"])


def test_validate_against_allowlist_tolerates_file_url_for_local_builds():
    """`file://` download_urls (curator-iterating-locally) are accepted
    even though they don't match a remote prefix — the source URL still has to."""
    pack = PackManifest.from_dict({
        **VALID_PACK,
        "download_url": "file:///tmp/local-build.tar.gz",
        "provenance": {"source": "https://github.com/mdn/content"},
    })
    validate_against_allowlist(pack, ["https://github.com/mdn/"])


def test_load_allowlist_returns_empty_when_missing(tmp_path):
    assert load_allowlist(tmp_path / "absent.json") == {}


def test_repo_allowlist_is_valid_and_covers_registry_sources():
    """Every catalog entry's provenance.source must be allowed by the
    shipped upstream allow-list. Stops a curator from sneaking in an
    unvetted upstream via a registry edit alone."""
    mcp_root = Path(__file__).resolve().parents[1]
    allowlist_path = mcp_root / "config" / "knowledge_packs_allowlist.json"
    assert allowlist_path.exists(), "allow-list file missing"
    allowlist = load_allowlist(allowlist_path)
    prefixes = allowlist.get("host_prefixes", [])
    assert isinstance(prefixes, list) and len(prefixes) > 0
    registry = load_registry(mcp_root / "config" / "knowledge_packs.json")
    for pack_id, pack in registry.items():
        # validate_against_allowlist raises PackError on miss; this asserts
        # every shipped catalog entry is allowed.
        validate_against_allowlist(pack, prefixes)


# ── Pack-archive helpers ────────────────────────────────────────────────

def test_sha256_of_file_matches_hashlib(tmp_path):
    p = tmp_path / "blob.bin"
    payload = b"the quick brown fox" * 1000
    p.write_bytes(payload)
    assert sha256_of_file(p) == hashlib.sha256(payload).hexdigest()


def test_verify_archive_sha256_passes_on_match(tmp_path):
    p = tmp_path / "ok.bin"
    p.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    verify_archive_sha256(p, digest)  # does not raise


def test_verify_archive_sha256_raises_on_mismatch(tmp_path):
    p = tmp_path / "bad.bin"
    p.write_bytes(b"hello")
    with pytest.raises(PackError, match="sha256 mismatch"):
        verify_archive_sha256(p, "0" * 64)


def test_verify_archive_sha256_skips_when_expected_empty(tmp_path):
    p = tmp_path / "any.bin"
    p.write_bytes(b"any")
    verify_archive_sha256(p, "")  # explicitly skipped, no raise


def test_parse_pack_json_round_trip():
    blob = json.dumps(VALID_PACK)
    pack = parse_pack_json(blob)
    assert pack.id == "cerid-starter-general"


@pytest.mark.parametrize("bad_member", [
    "/etc/passwd",
    "../escape.txt",
    "content/../../etc/passwd",
    "",
])
def test_assert_archive_path_safe_blocks_traversal(bad_member):
    with pytest.raises(PackError):
        assert_archive_path_safe(bad_member, archive_root="pack-1")


@pytest.mark.parametrize("ok_member", [
    "pack.json",
    "content/intro.md",
    "content/sub/dir/file.txt",
])
def test_assert_archive_path_safe_allows_safe_members(ok_member):
    assert_archive_path_safe(ok_member, archive_root="pack-1")  # no raise


# ── Install-state tracking ───────────────────────────────────────────────

def _record(pack_id="cerid-starter-general", version="1.0.0", artifact_ids=("a", "b")):
    return InstalledPack(
        pack_id=pack_id,
        version=version,
        installed_at="2026-05-10T12:00:00+00:00",
        domain="general",
        sha256="a" * 64,
        artifact_ids=tuple(artifact_ids),
    )


def test_install_state_round_trip():
    rec = _record()
    blob = serialise_install_state([rec])
    again = parse_install_state(blob)
    assert again == [rec]


def test_parse_install_state_treats_empty_as_empty():
    assert parse_install_state("") == []
    assert parse_install_state("   \n") == []


def test_parse_install_state_rejects_bad_schema():
    blob = json.dumps({"schema_version": 999, "packs": []})
    with pytest.raises(PackError, match="schema_version"):
        parse_install_state(blob)


def test_load_install_state_returns_empty_when_missing(tmp_path):
    assert load_install_state(tmp_path / "absent.json") == []


def test_save_install_state_is_atomic(tmp_path):
    p = tmp_path / "deep" / "state.json"
    rec = _record()
    save_install_state(p, [rec])
    assert p.exists()
    # After the rename the .tmp companion must not survive.
    assert not p.with_suffix(p.suffix + ".tmp").exists()
    # Schema version is written so future migrations can detect old files.
    payload = json.loads(p.read_text())
    assert payload["schema_version"] == INSTALL_STATE_SCHEMA_VERSION


def test_find_upsert_remove_installed():
    rec = _record(version="1.0.0")
    state: list[InstalledPack] = []
    state = upsert_installed(state, rec)
    assert find_installed(state, rec.pack_id) == rec
    assert find_installed(state, rec.pack_id, version="1.0.0") == rec
    assert find_installed(state, rec.pack_id, version="9.9.9") is None
    # Upsert a newer version: prior entry is replaced (not duplicated).
    rec_v2 = _record(version="1.1.0")
    state = upsert_installed(state, rec_v2)
    assert len(state) == 1
    assert state[0].version == "1.1.0"
    state = remove_installed(state, rec.pack_id)
    assert state == []
