# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Field-level Fernet encryption for sensitive metadata (opt-in via CERID_ENCRYPTION_KEY)."""

from __future__ import annotations

import hashlib
import logging
import os
import threading

logger = logging.getLogger("ai-companion.encryption")

_encryptor: FieldEncryptor | None = None
_initialized = False

ENCRYPTED_PREFIX = "enc:v1:"


def is_encryption_enabled() -> bool:
    """Check if encryption is configured and available."""
    key = os.getenv("CERID_ENCRYPTION_KEY", "")
    if not key:
        return False
    try:
        # Verify cryptography is available
        from cryptography.fernet import Fernet  # noqa: F401
        return True
    except ImportError:
        logger.warning(
            "CERID_ENCRYPTION_KEY is set but 'cryptography' package is not installed. "
            "Install with: pip install cryptography"
        )
        return False


class FieldEncryptor:
    """Symmetric field encryption using Fernet (AES-128-CBC + HMAC-SHA256)."""

    def __init__(self, key: str):
        from cryptography.fernet import Fernet

        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            raise ValueError(
                f"Invalid encryption key: {e}. "
                f"Generate a valid key with: "
                f'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from e

        self._key_hash = hashlib.sha256(key.encode() if isinstance(key, str) else key).hexdigest()[:16]

    @property
    def key_hash(self) -> str:
        """Return truncated hash of the encryption key (for verification, NOT the key itself)."""
        return self._key_hash

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string field value, returning a prefixed ciphertext string."""
        if not plaintext:
            return plaintext

        if plaintext.startswith(ENCRYPTED_PREFIX):
            return plaintext

        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return ENCRYPTED_PREFIX + token.decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a field value. Returns unencrypted values as-is (backward compatible)."""
        if not ciphertext:
            return ciphertext

        if not ciphertext.startswith(ENCRYPTED_PREFIX):
            return ciphertext  # Not encrypted — return as-is

        token = ciphertext[len(ENCRYPTED_PREFIX):]
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return ciphertext  # return raw value for key rotation debugging

    def encrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """Encrypt specified fields in a dictionary."""
        result = dict(data)
        for field in fields:
            if field in result and isinstance(result[field], str) and result[field]:
                result[field] = self.encrypt(result[field])
        return result

    def decrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """Decrypt specified fields in a dictionary."""
        result = dict(data)
        for field in fields:
            if field in result and isinstance(result[field], str) and result[field]:
                result[field] = self.decrypt(result[field])
        return result


_encryptor_lock = threading.Lock()


def get_encryptor() -> FieldEncryptor | None:
    """Get the singleton encryptor instance (None if not configured)."""
    global _encryptor, _initialized

    if _initialized:
        return _encryptor

    with _encryptor_lock:
        if _initialized:
            return _encryptor

        key = os.getenv("CERID_ENCRYPTION_KEY", "")

        if not key:
            logger.debug("Encryption disabled: CERID_ENCRYPTION_KEY not set")
            _encryptor = None
            _initialized = True
            return None

        try:
            _encryptor = FieldEncryptor(key)
            logger.info(f"Encryption enabled (key hash: {_encryptor.key_hash})")
            _initialized = True
            return _encryptor
        except (ImportError, ValueError) as e:
            logger.error(f"Encryption initialization failed: {e}")
            _encryptor = None
            _initialized = True
            return None


def encrypt_field(value: str) -> str:
    """Encrypt a single field if encryption is enabled, else return unchanged."""
    enc = get_encryptor()
    if enc is None:
        return value
    return enc.encrypt(value)


def decrypt_field(value: str) -> str:
    """Decrypt a single field if encryption is enabled, else return unchanged."""
    enc = get_encryptor()
    if enc is None:
        return value
    return enc.decrypt(value)


NEO4J_ENCRYPTED_FIELDS = ["filename", "summary", "keywords"]
REDIS_ENCRYPTED_FIELDS = ["filename", "domain", "extra"]
CHROMA_ENCRYPTED_FIELDS = ["filename", "summary"]


def reset_encryptor():
    """Reset the singleton (for testing only)."""
    global _encryptor, _initialized
    _encryptor = None
    _initialized = False
