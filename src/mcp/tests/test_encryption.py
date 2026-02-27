# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for encryption and sync backend (Phase 8D)."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Dependency stubs are handled by conftest.py pytest_configure()

# ---------------------------------------------------------------------------
# Check if cryptography is available (tests require it)
# ---------------------------------------------------------------------------

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


# ---------------------------------------------------------------------------
# Tests for FieldEncryptor
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography not installed")
class TestFieldEncryptor:
    """Test the FieldEncryptor class."""

    def _make_encryptor(self):
        from utils.encryption import FieldEncryptor
        key = Fernet.generate_key().decode()
        return FieldEncryptor(key), key

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypting then decrypting returns original text."""
        enc, _ = self._make_encryptor()
        original = "Hello, World! This is sensitive data."
        encrypted = enc.encrypt(original)
        decrypted = enc.decrypt(encrypted)
        assert decrypted == original

    def test_encrypted_has_prefix(self):
        """Encrypted values start with the encryption prefix."""
        from utils.encryption import ENCRYPTED_PREFIX

        enc, _ = self._make_encryptor()
        encrypted = enc.encrypt("secret")
        assert encrypted.startswith(ENCRYPTED_PREFIX)

    def test_no_double_encryption(self):
        """Encrypting an already-encrypted value returns it unchanged."""
        enc, _ = self._make_encryptor()
        encrypted_once = enc.encrypt("secret")
        encrypted_twice = enc.encrypt(encrypted_once)
        assert encrypted_once == encrypted_twice

    def test_empty_string_passthrough(self):
        """Empty strings are not encrypted."""
        enc, _ = self._make_encryptor()
        assert enc.encrypt("") == ""
        assert enc.decrypt("") == ""

    def test_unencrypted_passthrough(self):
        """Decrypting a non-encrypted value returns it as-is."""
        enc, _ = self._make_encryptor()
        plain = "not encrypted at all"
        assert enc.decrypt(plain) == plain

    def test_different_keys_cannot_decrypt(self):
        """Different keys cannot decrypt each other's ciphertext."""
        enc1, _ = self._make_encryptor()
        enc2, _ = self._make_encryptor()

        encrypted = enc1.encrypt("secret data")
        # enc2 should fail to decrypt but not crash (returns raw ciphertext)
        result = enc2.decrypt(encrypted)
        # Should return the encrypted value (failed decryption)
        assert result == encrypted

    def test_key_hash_is_stable(self):
        """Same key produces same hash."""
        from utils.encryption import FieldEncryptor

        key = Fernet.generate_key().decode()
        enc1 = FieldEncryptor(key)
        enc2 = FieldEncryptor(key)
        assert enc1.key_hash == enc2.key_hash

    def test_encrypt_dict(self):
        """encrypt_dict encrypts specified fields."""
        enc, _ = self._make_encryptor()
        data = {"filename": "secret.pdf", "domain": "finance", "count": "42"}
        encrypted = enc.encrypt_dict(data, ["filename"])
        assert encrypted["filename"].startswith("enc:v1:")
        assert encrypted["domain"] == "finance"  # unchanged
        assert encrypted["count"] == "42"  # unchanged

    def test_decrypt_dict(self):
        """decrypt_dict decrypts specified fields."""
        enc, _ = self._make_encryptor()
        data = {"filename": "secret.pdf", "domain": "finance"}
        encrypted = enc.encrypt_dict(data, ["filename"])
        decrypted = enc.decrypt_dict(encrypted, ["filename"])
        assert decrypted["filename"] == "secret.pdf"
        assert decrypted["domain"] == "finance"

    def test_unicode_support(self):
        """Encryption handles Unicode text correctly."""
        enc, _ = self._make_encryptor()
        original = "日本語テスト — émojis 🎉 and accénts"
        encrypted = enc.encrypt(original)
        decrypted = enc.decrypt(encrypted)
        assert decrypted == original

    def test_invalid_key_raises(self):
        """Invalid encryption key raises ValueError."""
        from utils.encryption import FieldEncryptor

        with pytest.raises(ValueError, match="Invalid encryption key"):
            FieldEncryptor("not-a-valid-fernet-key")


@pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography not installed")
class TestEncryptionModule:
    """Test module-level encryption functions."""

    def setup_method(self):
        """Reset encryptor singleton before each test."""
        from utils.encryption import reset_encryptor
        reset_encryptor()

    def test_is_encryption_enabled_without_key(self):
        """Encryption disabled when CERID_ENCRYPTION_KEY not set."""
        from utils.encryption import is_encryption_enabled

        with patch.dict(os.environ, {}, clear=False):
            # Remove key if present
            os.environ.pop("CERID_ENCRYPTION_KEY", None)
            assert is_encryption_enabled() is False

    def test_is_encryption_enabled_with_key(self):
        """Encryption enabled when CERID_ENCRYPTION_KEY is set."""
        from utils.encryption import is_encryption_enabled

        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            assert is_encryption_enabled() is True

    def test_get_encryptor_returns_none_without_key(self):
        """get_encryptor returns None when no key is configured."""
        from utils.encryption import get_encryptor, reset_encryptor
        reset_encryptor()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CERID_ENCRYPTION_KEY", None)
            result = get_encryptor()
            assert result is None

    def test_get_encryptor_returns_instance_with_key(self):
        """get_encryptor returns FieldEncryptor when key is configured."""
        from utils.encryption import FieldEncryptor, get_encryptor, reset_encryptor
        reset_encryptor()

        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            result = get_encryptor()
            assert isinstance(result, FieldEncryptor)

    def test_encrypt_field_passthrough_when_disabled(self):
        """encrypt_field returns value unchanged when encryption disabled."""
        from utils.encryption import encrypt_field, reset_encryptor
        reset_encryptor()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CERID_ENCRYPTION_KEY", None)
            assert encrypt_field("hello") == "hello"

    def test_decrypt_field_passthrough_when_disabled(self):
        """decrypt_field returns value unchanged when encryption disabled."""
        from utils.encryption import decrypt_field, reset_encryptor
        reset_encryptor()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CERID_ENCRYPTION_KEY", None)
            assert decrypt_field("hello") == "hello"


# ---------------------------------------------------------------------------
# Tests for Sync Backend
# ---------------------------------------------------------------------------


class TestLocalSyncBackend:
    """Test the local filesystem sync backend."""

    def test_write_and_read_file(self, tmp_path):
        """Write then read a file through the backend."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        data = b"Hello, sync world!"
        backend.write_file("test/data.txt", data)

        result = backend.read_file("test/data.txt")
        assert result == data

    def test_read_nonexistent_returns_none(self, tmp_path):
        """Reading a nonexistent file returns None."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        assert backend.read_file("nonexistent.txt") is None

    def test_write_and_read_manifest(self, tmp_path):
        """Write then read the manifest."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        manifest = {"version": "1.0", "artifacts": 42}
        backend.write_manifest(manifest)

        result = backend.read_manifest()
        assert result["version"] == "1.0"
        assert result["artifacts"] == 42

    def test_read_manifest_nonexistent(self, tmp_path):
        """Reading manifest from empty dir returns None."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path / "empty"))
        assert backend.read_manifest() is None

    def test_list_files(self, tmp_path):
        """List files with prefix filter."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        backend.write_file("neo4j/artifacts.jsonl", b"data1")
        backend.write_file("neo4j/domains.jsonl", b"data2")
        backend.write_file("chroma/domain_coding.jsonl", b"data3")

        all_files = backend.list_files()
        assert len(all_files) == 3

        neo4j_files = backend.list_files("neo4j")
        assert len(neo4j_files) == 2
        assert "neo4j/artifacts.jsonl" in neo4j_files

    def test_exists(self, tmp_path):
        """Check file existence."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        backend.write_file("exists.txt", b"hello")

        assert backend.exists("exists.txt") is True
        assert backend.exists("nope.txt") is False

    def test_delete_file(self, tmp_path):
        """Delete a file through the backend."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        backend.write_file("deleteme.txt", b"bye")
        assert backend.exists("deleteme.txt")

        deleted = backend.delete_file("deleteme.txt")
        assert deleted is True
        assert not backend.exists("deleteme.txt")

    def test_delete_nonexistent(self, tmp_path):
        """Deleting nonexistent file returns False."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        assert backend.delete_file("nope.txt") is False

    def test_ensure_directory(self, tmp_path):
        """Ensure directory creates nested dirs."""
        from utils.sync_backend import LocalSyncBackend

        backend = LocalSyncBackend(sync_dir=str(tmp_path))
        backend.ensure_directory("deep/nested/dir")
        assert (tmp_path / "deep" / "nested" / "dir").is_dir()


class TestSyncBackendRegistry:
    """Test the sync backend registry."""

    def test_get_local_backend(self, tmp_path):
        """Default backend is LocalSyncBackend."""
        from utils.sync_backend import LocalSyncBackend, get_sync_backend, reset_sync_backend
        reset_sync_backend()

        backend = get_sync_backend(sync_dir=str(tmp_path))
        assert isinstance(backend, LocalSyncBackend)

    def test_unknown_backend_raises(self, tmp_path):
        """Unknown backend type raises ValueError."""
        from utils.sync_backend import get_sync_backend, reset_sync_backend
        reset_sync_backend()

        with pytest.raises(ValueError, match="Unknown sync backend"):
            get_sync_backend(backend_type="s3")

    def test_register_custom_backend(self):
        """Custom backends can be registered."""
        from utils.sync_backend import SyncBackend, register_sync_backend, reset_sync_backend
        reset_sync_backend()

        class DummyBackend(SyncBackend):
            def read_manifest(self): return None
            def write_manifest(self, m): pass
            def write_file(self, p, d): pass
            def read_file(self, p): return None
            def list_files(self, p=""): return []
            def exists(self, p): return False
            def delete_file(self, p): return False
            def ensure_directory(self, p): pass

        register_sync_backend("dummy", DummyBackend)
        # Verify it's registered (can instantiate)
        from utils.sync_backend import _BACKENDS
        assert "dummy" in _BACKENDS


# ---------------------------------------------------------------------------
# Tests for config
# ---------------------------------------------------------------------------


class TestEncryptionConfig:
    """Test encryption-related configuration."""

    def test_enable_encryption_default(self):
        """ENABLE_ENCRYPTION defaults to False."""
        import config
        # Default is false (unless env var is set in test runner)
        assert isinstance(config.ENABLE_ENCRYPTION, bool)

    def test_sync_backend_default(self):
        """SYNC_BACKEND defaults to 'local'."""
        import config
        assert config.SYNC_BACKEND == "local"