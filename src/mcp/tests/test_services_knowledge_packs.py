# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Service-level tests for knowledge-pack install / uninstall.

All side effects are dependency-injected — tests build a fake archive
on disk, drive ``install_pack`` with stub ``download`` / ``ingest``
callables, and assert the resulting install-state record. No httpx,
no chromadb, no Neo4j.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.knowledge_packs import (
    _default_download,
    _validate_embedded_manifest,
    install_pack,
    uninstall_pack,
)
from core.knowledge.packs import (
    InstalledPack,
    PackError,
    PackManifest,
    load_install_state,
)

# ── Helpers ───────────────────────────────────────────────────────────────

def _build_pack_archive(
    tmp_path: Path,
    *,
    pack_id: str = "starter-test",
    version: str = "1.0.0",
    domain: str = "general",
    sub_category: str = "reference",
    files: dict[str, bytes] | None = None,
    bad_member: str | None = None,
    embedded_id_override: str | None = None,
    embedded_version_override: str | None = None,
) -> tuple[Path, str, PackManifest]:
    """Build a tar.gz pack on disk + return ``(archive_path, sha256, manifest)``."""
    files = files or {
        "intro.md": b"# Hello\n\nA quick reference doc.\n",
        "subdir/deep.md": b"## Deep\n\nNested file.\n",
    }
    embedded_manifest = {
        "id": embedded_id_override or pack_id,
        "name": "Test pack",
        "version": embedded_version_override or version,
        "description": "Fixture pack for unit tests",
        "domain": domain,
        "sub_category": sub_category,
        "tags": ["fixture"],
        "license": "CC0-1.0",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        pack_blob = json.dumps(embedded_manifest).encode("utf-8")
        info = tarfile.TarInfo(name="pack.json")
        info.size = len(pack_blob)
        tf.addfile(info, io.BytesIO(pack_blob))
        for rel, payload in files.items():
            info = tarfile.TarInfo(name=f"content/{rel}")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        if bad_member:
            payload = b"escape!"
            info = tarfile.TarInfo(name=bad_member)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

    archive_bytes = buf.getvalue()
    archive_path = tmp_path / "pack.tar.gz"
    archive_path.write_bytes(archive_bytes)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    manifest = PackManifest.from_dict({
        "id": pack_id,
        "name": "Test pack",
        "version": version,
        "description": "Fixture pack for unit tests",
        "domain": domain,
        "sub_category": sub_category,
        "tags": ["fixture"],
        "license": "CC0-1.0",
        "size_bytes": len(archive_bytes),
        "artifact_count": len(files),
        "download_url": "https://example.org/pack.tar.gz",
        "sha256": digest,
    })
    return archive_path, digest, manifest


def _make_download_stub(archive_path: Path):
    """Return a download stub that copies the prebuilt archive into place."""

    async def _download(url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive_path.read_bytes())

    return _download


def _make_ingest_stub():
    """Return ``(ingest_fn, calls_list)`` capturing every ingest invocation."""
    calls: list[dict] = []

    async def _ingest(file_path, domain, sub_category, tags, provenance):
        artifact_id = f"art-{len(calls)}"
        calls.append({
            "file_path": str(file_path),
            "domain": domain,
            "sub_category": sub_category,
            "tags": tuple(tags),
            "provenance": dict(provenance),
            "artifact_id": artifact_id,
        })
        return {"status": "success", "artifact_id": artifact_id}

    return _ingest, calls


# ── install_pack happy path ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_install_pack_happy_path(tmp_path):
    archive_path, _, manifest = _build_pack_archive(tmp_path)
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()
    state_path = tmp_path / "state" / "installed_packs.json"
    staging_root = tmp_path / "staging"

    record = await install_pack(
        manifest,
        state_path=state_path,
        staging_root=staging_root,
        download=download,
        ingest=ingest,
    )

    assert record.pack_id == manifest.id
    assert record.version == manifest.version
    assert len(record.artifact_ids) == 2  # the two content files
    assert {c["domain"] for c in calls} == {manifest.domain}
    # Pack-level sub_category propagates to ingest by default.
    assert {c["sub_category"] for c in calls} == {manifest.sub_category}
    # Tags include pack id + version provenance markers.
    for c in calls:
        assert f"pack:{manifest.id}" in c["tags"]
        assert f"pack-version:{manifest.version}" in c["tags"]
    # State file persisted, schema versioned.
    assert state_path.exists()
    persisted = load_install_state(state_path)
    assert len(persisted) == 1
    assert persisted[0] == record
    # Staging cleaned up by default.
    assert not (staging_root / f"{manifest.id}-{manifest.version}").exists()


@pytest.mark.asyncio
async def test_install_pack_idempotent_same_version(tmp_path):
    archive_path, _, manifest = _build_pack_archive(tmp_path)
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()
    state_path = tmp_path / "state.json"
    staging_root = tmp_path / "staging"

    first = await install_pack(
        manifest, state_path=state_path, staging_root=staging_root,
        download=download, ingest=ingest,
    )
    second = await install_pack(
        manifest, state_path=state_path, staging_root=staging_root,
        download=download, ingest=ingest,
    )
    assert first == second
    # Second invocation must not re-trigger ingest.
    assert len(calls) == 2  # one per file, only on the first install


@pytest.mark.asyncio
async def test_install_pack_version_bump_removes_prior_artifacts(tmp_path):
    """AF-051: bumping a pack's version removes the prior version's artifacts
    through the content-lifecycle coordinator, so Chroma/Neo4j don't accumulate
    orphans across upgrades."""
    state_path = tmp_path / "state.json"
    staging_root = tmp_path / "staging"
    ingest, calls = _make_ingest_stub()

    v1dir = tmp_path / "v1"
    v1dir.mkdir()
    archive1, _, manifest1 = _build_pack_archive(v1dir, version="1.0.0")
    rec1 = await install_pack(
        manifest1, state_path=state_path, staging_root=staging_root,
        download=_make_download_stub(archive1), ingest=ingest,
    )
    v1_ids = set(rec1.artifact_ids)
    assert v1_ids  # the two content files

    v2dir = tmp_path / "v2"
    v2dir.mkdir()
    archive2, _, manifest2 = _build_pack_archive(v2dir, version="2.0.0")
    removed: list[str] = []

    def _fake_remove(aid, neo4j=None):
        removed.append(aid)
        return {"ok": True}

    with patch("app.services.content_lifecycle.remove_content", side_effect=_fake_remove), \
         patch("app.deps.get_neo4j", return_value=object()):
        rec2 = await install_pack(
            manifest2, state_path=state_path, staging_root=staging_root,
            download=_make_download_stub(archive2), ingest=ingest,
        )

    assert rec2.version == "2.0.0"
    # Prior version's artifacts removed via the coordinator; new ones untouched.
    assert set(removed) == v1_ids
    assert not (set(removed) & set(rec2.artifact_ids))
    # State now records only the new version.
    persisted = load_install_state(state_path)
    assert len(persisted) == 1
    assert persisted[0].version == "2.0.0"


@pytest.mark.asyncio
async def test_install_pack_per_file_overrides(tmp_path):
    archive_path, digest, _ = _build_pack_archive(tmp_path)
    # Override sub_category on one of the files via the manifest's `files` list.
    manifest = PackManifest.from_dict({
        "id": "starter-test",
        "name": "Test pack",
        "version": "1.0.0",
        "description": "fixture",
        "domain": "general",
        "sub_category": "reference",
        "tags": ["fixture"],
        "license": "CC0-1.0",
        "download_url": "https://example.org/pack.tar.gz",
        "sha256": digest,
        "files": [
            {"path": "content/intro.md", "sub_category": "intro-special"},
        ],
    })

    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()

    await install_pack(
        manifest,
        state_path=tmp_path / "state.json",
        staging_root=tmp_path / "staging",
        download=download, ingest=ingest,
    )

    by_path = {Path(c["file_path"]).name: c for c in calls}
    assert by_path["intro.md"]["sub_category"] == "intro-special"
    # Other file falls back to pack-level sub_category.
    other = next(c for c in calls if Path(c["file_path"]).name == "deep.md")
    assert other["sub_category"] == "reference"


@pytest.mark.asyncio
async def test_install_pack_fails_on_sha256_mismatch(tmp_path):
    archive_path, _, manifest_ok = _build_pack_archive(tmp_path)
    bad_manifest = PackManifest.from_dict({
        **manifest_ok.to_dict(),
        "sha256": "0" * 64,
    })
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()

    with pytest.raises(PackError, match="sha256 mismatch"):
        await install_pack(
            bad_manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=download, ingest=ingest,
        )
    assert calls == []  # never reached the ingest step


@pytest.mark.asyncio
async def test_install_pack_rejects_archive_traversal(tmp_path):
    archive_path, _, manifest = _build_pack_archive(
        tmp_path, bad_member="../../escape.txt",
    )
    # sha256 was computed against the archive that contains the bad member,
    # so re-derive a manifest with the right digest.
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest = PackManifest.from_dict({**manifest.to_dict(), "sha256": digest})
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()

    with pytest.raises(PackError, match="Path-traversal"):
        await install_pack(
            manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=download, ingest=ingest,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_install_pack_rejects_missing_pack_json(tmp_path):
    """Archive without a top-level pack.json must be rejected."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"hello"
        info = tarfile.TarInfo(name="content/intro.md")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    archive_path = tmp_path / "pack.tar.gz"
    archive_path.write_bytes(buf.getvalue())
    digest = hashlib.sha256(buf.getvalue()).hexdigest()
    manifest = PackManifest.from_dict({
        "id": "starter-test", "name": "x", "version": "1.0.0",
        "description": "x", "domain": "general",
        "license": "CC0-1.0",
        "download_url": "https://example.org/x.tar.gz", "sha256": digest,
    })
    with pytest.raises(PackError, match="missing required pack.json"):
        await install_pack(
            manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=_make_download_stub(archive_path),
            ingest=_make_ingest_stub()[0],
        )


@pytest.mark.asyncio
async def test_install_pack_rejects_embedded_manifest_mismatch(tmp_path):
    archive_path, digest, manifest = _build_pack_archive(
        tmp_path, embedded_id_override="other-id",
    )
    manifest = PackManifest.from_dict({**manifest.to_dict(), "sha256": digest})
    with pytest.raises(PackError, match="does not match registry id"):
        await install_pack(
            manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=_make_download_stub(archive_path),
            ingest=_make_ingest_stub()[0],
        )


@pytest.mark.asyncio
async def test_install_pack_no_download_url_raises(tmp_path):
    manifest = PackManifest.from_dict({
        "id": "no-url", "name": "x", "version": "1.0.0",
        "description": "x", "domain": "general",
        "license": "CC0-1.0",
        "download_url": "",
    })
    with pytest.raises(PackError, match="no tarball has been published"):
        await install_pack(
            manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=_make_download_stub(tmp_path),  # never called
            ingest=_make_ingest_stub()[0],
        )


@pytest.mark.asyncio
async def test_install_pack_tolerates_per_file_ingest_failure(tmp_path):
    archive_path, _, manifest = _build_pack_archive(tmp_path, files={
        "ok.md": b"ok",
        "boom.md": b"boom",
    })

    async def _ingest(file_path, domain, sub_category, tags, provenance):
        if Path(file_path).name == "boom.md":
            raise RuntimeError("simulated ingest failure")
        return {"status": "success", "artifact_id": f"art-{Path(file_path).name}"}

    record = await install_pack(
        manifest,
        state_path=tmp_path / "state.json",
        staging_root=tmp_path / "staging",
        download=_make_download_stub(archive_path), ingest=_ingest,
    )
    # Half-installed packs are still recorded; the failed file is just
    # missing from artifact_ids. Re-install (after fixing the issue) is
    # safe because content_hash dedup handles duplicates.
    assert len(record.artifact_ids) == 1
    assert record.artifact_ids[0] == "art-ok.md"


# ── uninstall_pack ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uninstall_pack_removes_artifacts_and_state(tmp_path):
    state_path = tmp_path / "state.json"
    record = InstalledPack(
        pack_id="cerid-starter",
        version="1.0.0",
        installed_at="2026-05-10T00:00:00+00:00",
        domain="general",
        sha256="a" * 64,
        artifact_ids=("a", "b", "c"),
    )
    from core.knowledge.packs import save_install_state
    save_install_state(state_path, [record])

    deleted: list[str] = []

    async def _delete(artifact_id: str):
        deleted.append(artifact_id)
        # b is missing on the backend (e.g. operator manually deleted it).
        return {"deleted": artifact_id != "b", "artifact_id": artifact_id}

    summary = await uninstall_pack(
        "cerid-starter", state_path=state_path, delete=_delete,
    )
    assert summary == {
        "removed": 2, "missing": 1,
        "pack_id": "cerid-starter", "status": "uninstalled",
    }
    assert deleted == ["a", "b", "c"]
    assert load_install_state(state_path) == []


@pytest.mark.asyncio
async def test_uninstall_pack_when_not_installed_returns_no_op(tmp_path):
    state_path = tmp_path / "state.json"

    async def _delete(artifact_id):
        raise AssertionError("delete should not be called")

    summary = await uninstall_pack(
        "missing-pack", state_path=state_path, delete=_delete,
    )
    assert summary["status"] == "not_installed"
    assert summary["removed"] == 0


# ── _default_download file:// support ──────────────────────────────────

@pytest.mark.asyncio
async def test_default_download_supports_file_scheme(tmp_path):
    src = tmp_path / "src.tar.gz"
    src.write_bytes(b"hello pack")
    dest = tmp_path / "out" / "copy.tar.gz"
    await _default_download(src.resolve().as_uri(), dest)
    assert dest.read_bytes() == b"hello pack"


@pytest.mark.asyncio
async def test_default_download_rejects_remote_file_url(tmp_path):
    with pytest.raises(ValueError, match="non-local netloc"):
        await _default_download("file://other-host/some/path", tmp_path / "out.bin")


@pytest.mark.asyncio
async def test_default_download_file_url_missing_source_raises(tmp_path):
    missing_src = (tmp_path / "missing.tar.gz").resolve()
    with pytest.raises(FileNotFoundError):
        await _default_download(missing_src.as_uri(), tmp_path / "out.tar.gz")


# ── install-time license-category gate ────────────────────────────────

@pytest.mark.asyncio
async def test_install_pack_refuses_unknown_license(tmp_path):
    archive_path, _, manifest_ok = _build_pack_archive(tmp_path)
    bad_manifest = PackManifest.from_dict({
        **manifest_ok.to_dict(),
        "license": "Proprietary-Acme-2027",  # not in SPDX category map
    })
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()
    with pytest.raises(PackError, match="not in the recognized SPDX list"):
        await install_pack(
            bad_manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=download, ingest=ingest,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_install_pack_refuses_share_alike_by_default(tmp_path):
    """share_alike is intentionally excluded from DEFAULT_INSTALL_CATEGORIES."""
    archive_path, _, manifest_ok = _build_pack_archive(tmp_path)
    sa_manifest = PackManifest.from_dict({
        **manifest_ok.to_dict(),
        "license": "CC-BY-SA-4.0",
    })
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()
    with pytest.raises(PackError, match="share_alike"):
        await install_pack(
            sa_manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=download, ingest=ingest,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_install_pack_accepts_share_alike_when_explicitly_allowed(tmp_path):
    archive_path, _, manifest_ok = _build_pack_archive(tmp_path)
    sa_manifest = PackManifest.from_dict({
        **manifest_ok.to_dict(),
        "license": "CC-BY-SA-4.0",
    })
    record = await install_pack(
        sa_manifest,
        state_path=tmp_path / "state.json",
        staging_root=tmp_path / "staging",
        download=_make_download_stub(archive_path),
        ingest=_make_ingest_stub()[0],
        allowed_license_categories=frozenset(
            {"public_domain", "permissive", "attribution", "share_alike"}
        ),
    )
    assert record.pack_id == sa_manifest.id


@pytest.mark.asyncio
async def test_install_pack_refuses_planned_entry_with_clear_message(tmp_path):
    """Catalog entries with empty download_url surface a curator-actionable error."""
    manifest = PackManifest.from_dict({
        "id": "planned-x",
        "name": "Planned",
        "version": "0.0.0",
        "description": "catalog-only entry",
        "domain": "general",
        "license": "CC0-1.0",
        "download_url": "",
        "provenance": {"source": "https://github.com/mdn/"},
    })
    with pytest.raises(PackError, match="no tarball has been published"):
        await install_pack(
            manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=_make_download_stub(tmp_path),  # never reached
            ingest=_make_ingest_stub()[0],
        )


# ── Phase 8a provenance metadata propagation ──────────────────────────

@pytest.mark.asyncio
async def test_install_pack_stamps_pack_provenance_on_each_chunk(tmp_path):
    """Every ingest call carries source_url + license_spdx + adapter."""
    archive_path, _, manifest_ok = _build_pack_archive(tmp_path)
    manifest = PackManifest.from_dict({
        **manifest_ok.to_dict(),
        "license": "CC-BY-4.0",
        "provenance": {
            "source": "https://github.com/mdn/content",
            "curator": "Mozilla",
        },
        "build": {"adapter": "github_zip", "config": {}},
    })
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()
    await install_pack(
        manifest,
        state_path=tmp_path / "state.json",
        staging_root=tmp_path / "staging",
        download=download, ingest=ingest,
    )
    assert calls, "expected at least one ingest invocation"
    for c in calls:
        prov = c["provenance"]
        assert prov["pack_id"] == manifest.id
        assert prov["pack_version"] == manifest.version
        assert prov["pack_license_spdx"] == "CC-BY-4.0"
        assert prov["pack_license_category"] == "attribution"
        assert prov["pack_source_url"] == "https://github.com/mdn/content"
        assert prov["pack_curator"] == "Mozilla"
        assert prov["pack_adapter"] == "github_zip"
        assert prov["pack_sha256"] == manifest.sha256
        # retrieved_at is ISO-8601-shaped
        assert "T" in prov["pack_retrieved_at"]
        # per-file marker
        assert prov["pack_file"].startswith("content/")


@pytest.mark.asyncio
async def test_install_pack_provenance_falls_back_to_tarball_when_no_build(tmp_path):
    """Catalog packs without a build spec still get pack_adapter='tarball'."""
    archive_path, _, manifest_ok = _build_pack_archive(tmp_path)
    # Strip build spec to simulate a hand-published tarball with no recipe.
    raw = manifest_ok.to_dict()
    raw.pop("build", None)
    manifest = PackManifest.from_dict(raw)
    download = _make_download_stub(archive_path)
    ingest, calls = _make_ingest_stub()
    await install_pack(
        manifest,
        state_path=tmp_path / "state.json",
        staging_root=tmp_path / "staging",
        download=download, ingest=ingest,
    )
    assert all(c["provenance"]["pack_adapter"] == "tarball" for c in calls)


# ── _validate_embedded_manifest direct ─────────────────────────────────

def test_validate_embedded_manifest_version_mismatch_raises():
    registry_pack = PackManifest.from_dict({
        "id": "x", "name": "x", "version": "1.0.0",
        "description": "x", "domain": "general",
        "download_url": "https://x", "sha256": "a" * 64,
    })
    embedded = json.dumps({
        "id": "x", "name": "x", "version": "2.0.0",
        "description": "x", "domain": "general",
    }).encode()
    with pytest.raises(PackError, match="does not match registry version"):
        _validate_embedded_manifest(embedded, registry_pack)


# ── Post-install recompute trigger (Fix 1) ────────────────────────────────

@pytest.mark.asyncio
async def test_install_pack_enqueues_trust_and_umap_jobs(tmp_path):
    """A successful install must enqueue ComputeTrustStateJob + ComputeUmap3DJob."""
    archive_path, _, manifest = _build_pack_archive(tmp_path)
    download = _make_download_stub(archive_path)
    ingest, _ = _make_ingest_stub()

    mock_enqueue = MagicMock()
    fake_trust_job = MagicMock()
    fake_umap_job = MagicMock()
    fake_trust_cls = MagicMock(return_value=fake_trust_job)
    fake_umap_cls = MagicMock(return_value=fake_umap_job)

    with patch.dict("sys.modules", {
        "app.db.redis.processor_queue": MagicMock(enqueue_job=mock_enqueue),
        "app.processor.jobs.compute_trust_state": MagicMock(ComputeTrustStateJob=fake_trust_cls),
        "app.processor.jobs.compute_umap_3d": MagicMock(ComputeUmap3DJob=fake_umap_cls),
    }):
        record = await install_pack(
            manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=download,
            ingest=ingest,
        )

    assert record.pack_id == manifest.id
    assert mock_enqueue.call_count == 2
    enqueued_jobs = [c[0][0] for c in mock_enqueue.call_args_list]
    assert fake_trust_job in enqueued_jobs, "ComputeTrustStateJob must be enqueued"
    assert fake_umap_job in enqueued_jobs, "ComputeUmap3DJob must be enqueued"


@pytest.mark.asyncio
async def test_install_pack_recompute_queue_failure_does_not_raise(tmp_path):
    """A failure in the recompute enqueue block must not abort a successful install."""
    archive_path, _, manifest = _build_pack_archive(tmp_path)
    download = _make_download_stub(archive_path)
    ingest, _ = _make_ingest_stub()

    # Simulate enqueue_job raising (e.g. Redis unavailable).
    exploding_module = MagicMock()
    exploding_module.enqueue_job.side_effect = RuntimeError("Redis unavailable")
    trust_module = MagicMock()
    umap_module = MagicMock()

    with patch.dict("sys.modules", {
        "app.db.redis.processor_queue": exploding_module,
        "app.processor.jobs.compute_trust_state": trust_module,
        "app.processor.jobs.compute_umap_3d": umap_module,
    }):
        # Must NOT raise — best-effort block swallows the error.
        record = await install_pack(
            manifest,
            state_path=tmp_path / "state.json",
            staging_root=tmp_path / "staging",
            download=download,
            ingest=ingest,
        )

    assert record.pack_id == manifest.id, (
        "install_pack must succeed even when the recompute enqueue fails"
    )


# ── Processor-queue introspection (async install status) ─────────────────


class _FakeQueueRedis:
    """Minimal stand-in for the processor queue's Redis key layout."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def lrange(self, key, start, end):
        return list(self.lists.get(key, []))

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def add_job(self, job_id: str, *, job_type: str, pack_id: str, where: str):
        self.hashes[f"cerid:proc:job:{job_id}"] = {
            "job_type": job_type,
            "payload": json.dumps({"pack_id": pack_id}),
        }
        if where == "running":
            self.sets.setdefault("cerid:proc:running", set()).add(job_id)
        else:
            self.lists.setdefault(f"cerid:proc:queue:{where}", []).append(job_id)


def test_active_install_jobs_reports_queued_and_running():
    from app.services.knowledge_packs import active_install_jobs

    r = _FakeQueueRedis()
    r.add_job("j1", job_type="knowledge_pack_install", pack_id="pack-a", where="high")
    r.add_job("j2", job_type="knowledge_pack_install", pack_id="pack-b", where="running")
    r.add_job("j3", job_type="wiki_refresh", pack_id="pack-c", where="high")

    active = active_install_jobs(redis_client=r)
    assert active == {"pack-a": "j1", "pack-b": "j2"}


def test_active_install_jobs_empty_queue():
    from app.services.knowledge_packs import active_install_jobs

    assert active_install_jobs(redis_client=_FakeQueueRedis()) == {}


def test_active_install_jobs_tolerates_malformed_payload():
    from app.services.knowledge_packs import active_install_jobs

    r = _FakeQueueRedis()
    r.add_job("j1", job_type="knowledge_pack_install", pack_id="pack-a", where="high")
    r.hashes["cerid:proc:job:j1"]["payload"] = "{ not json"

    assert active_install_jobs(redis_client=r) == {}


def test_enqueue_install_job_persists_payload():
    from app.services.knowledge_packs import active_install_jobs, enqueue_install_job

    class _RecordingRedis(_FakeQueueRedis):
        def hset(self, key, mapping=None):
            self.hashes[key] = dict(mapping or {})

        def lpush(self, key, value):
            self.lists.setdefault(key, []).insert(0, value)

    r = _RecordingRedis()
    job_id = enqueue_install_job("pack-x", redis_client=r)

    assert job_id
    # The enqueued job must round-trip through the introspection helper —
    # the registry endpoint's "installing" flag depends on this.
    assert active_install_jobs(redis_client=r) == {"pack-x": job_id}
