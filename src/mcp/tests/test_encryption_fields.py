# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the encryption field-list honesty pass (Task 2.6a).

Covers: the pruned ``NEO4J_ENCRYPTED_FIELDS`` / ``REDIS_ENCRYPTED_FIELDS`` /
``CHROMA_ENCRYPTED_FIELDS`` lists, the ``_encrypt_chroma_metadata`` helper
wired into both Chroma upsert paths in ``app/services/ingestion.py``, and
the ``/health`` encryption block.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


@pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography not installed")
class TestEncryptChromaMetadata:
    """``_encrypt_chroma_metadata`` — opt-in, only the declared field, idempotent."""

    def setup_method(self):
        from utils.encryption import reset_encryptor
        reset_encryptor()

    def teardown_method(self):
        from utils.encryption import reset_encryptor
        reset_encryptor()

    def test_encrypts_only_declared_field_with_key(self):
        """summary is encrypted; filename and other keys are left unchanged."""
        from app.services.ingestion import _encrypt_chroma_metadata

        key = Fernet.generate_key().decode()
        meta = {
            "filename": "secret_plan.pdf",
            "summary": "A plan to do secret things.",
            "domain": "finance",
            "chunk_index": 3,
        }
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            result = _encrypt_chroma_metadata(meta)

        assert result["summary"].startswith("enc:v1:")
        assert result["summary"] != meta["summary"]
        assert result["filename"] == "secret_plan.pdf"
        assert result["domain"] == "finance"
        assert result["chunk_index"] == 3

    def test_roundtrip_decrypt_recovers_original(self):
        """decrypt_field recovers the original summary text."""
        from app.services.ingestion import _encrypt_chroma_metadata
        from utils.encryption import decrypt_field

        key = Fernet.generate_key().decode()
        original = "The quarterly roadmap in plain English."
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            result = _encrypt_chroma_metadata({"summary": original, "filename": "x.md"})
            assert decrypt_field(result["summary"]) == original

    def test_no_key_leaves_metadata_unchanged(self):
        """Without CERID_ENCRYPTION_KEY, the dict is returned unchanged (opt-in default)."""
        from app.services.ingestion import _encrypt_chroma_metadata

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CERID_ENCRYPTION_KEY", None)
            meta = {"filename": "plain.pdf", "summary": "plain text summary"}
            result = _encrypt_chroma_metadata(meta)

        assert result == meta
        assert not result["summary"].startswith("enc:v1:")

    def test_idempotent_no_double_encryption(self):
        """Encrypting an already-encrypted metadata dict does not double-encrypt."""
        from app.services.ingestion import _encrypt_chroma_metadata

        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            once = _encrypt_chroma_metadata({"summary": "hello world", "filename": "f.txt"})
            twice = _encrypt_chroma_metadata(once)

        assert once["summary"] == twice["summary"]

    def test_does_not_mutate_input_dict(self):
        """The helper returns a new dict — base_meta must stay plaintext for Neo4j."""
        from app.services.ingestion import _encrypt_chroma_metadata

        key = Fernet.generate_key().decode()
        original_meta = {"summary": "do not mutate me", "filename": "f.txt"}
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            _encrypt_chroma_metadata(original_meta)

        assert original_meta["summary"] == "do not mutate me"

    def test_fails_open_on_encrypt_error_keeps_plaintext(self):
        """Task 2.6a: an ``encrypt_field`` error (e.g. a lone Unicode surrogate
        that ``.encode("utf-8")`` cannot handle) must not abort the ingest —
        the field is left in plaintext instead of raising.
        """
        from app.services.ingestion import _encrypt_chroma_metadata

        key = Fernet.generate_key().decode()
        meta = {"summary": "a summary with a lone surrogate \ud800", "filename": "f.txt"}
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            with patch(
                "app.services.ingestion.encrypt_field",
                side_effect=UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"),
            ):
                result = _encrypt_chroma_metadata(meta)

        assert result["summary"] == meta["summary"]
        assert not result["summary"].startswith("enc:v1:")


class TestEncryptedFieldListsAreTruthful:
    """The declared field lists must match what is actually wired (Task 2.6a)."""

    def test_neo4j_and_redis_lists_are_empty(self):
        from utils.encryption import NEO4J_ENCRYPTED_FIELDS, REDIS_ENCRYPTED_FIELDS

        assert NEO4J_ENCRYPTED_FIELDS == []
        assert REDIS_ENCRYPTED_FIELDS == []

    def test_chroma_list_is_summary_only(self):
        from utils.encryption import CHROMA_ENCRYPTED_FIELDS

        assert CHROMA_ENCRYPTED_FIELDS == ["summary"]


class TestHealthEncryptionBlock:
    """``/health`` reports an honest encryption block."""

    def _call_health(self):
        from app.routers.health import health_check
        return health_check()

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_encryption_block_without_key(self, mock_chroma, mock_neo4j, mock_redis):
        from utils.encryption import reset_encryptor

        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()

        reset_encryptor()
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CERID_ENCRYPTION_KEY", None)
                result = self._call_health()
        finally:
            reset_encryptor()

        assert "encryption" in result
        assert result["encryption"]["key_present"] is False
        assert result["encryption"]["fields_covered"] == 1

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography not installed")
    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_encryption_block_with_key(self, mock_chroma, mock_neo4j, mock_redis):
        from utils.encryption import reset_encryptor

        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()

        key = Fernet.generate_key().decode()
        reset_encryptor()
        try:
            with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
                result = self._call_health()
        finally:
            reset_encryptor()

        assert result["encryption"]["key_present"] is True
        assert result["encryption"]["fields_covered"] == 1

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_encryption_block_with_malformed_key_reports_false(
        self, mock_chroma, mock_neo4j, mock_redis,
    ):
        """Task 2.6a: a malformed key must NOT report ``key_present: true``.

        ``is_encryption_enabled()`` only checks the env var is set (+ the
        ``cryptography`` package is importable) — a malformed key would still
        report ``True`` there even though ``FieldEncryptor`` construction
        fails and ``get_encryptor()`` returns ``None``. ``/health`` must
        reflect the actually-operational state.
        """
        from utils.encryption import reset_encryptor

        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()

        reset_encryptor()
        try:
            with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": "not-a-valid-fernet-key"}):
                result = self._call_health()
        finally:
            reset_encryptor()

        assert result["encryption"]["key_present"] is False
