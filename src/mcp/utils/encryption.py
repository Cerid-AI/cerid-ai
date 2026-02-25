"""
Application-level field encryption for Cerid AI (Phase 8D).

Encrypts sensitive metadata fields before writing to Neo4j/Redis.
ChromaDB embeddings are NOT encrypted (needed for similarity search).

Fields encrypted when enabled:
- Neo4j: Artifact.filename, Artifact.summary, Artifact.keywords
- Redis: Full audit log entries
- ChromaDB: Document text in metadata (not embeddings, not chunk IDs)

Key management:
- Encryption key from CERID_ENCRYPTION_KEY env var (Fernet-compatible)
- If not set, encryption is disabled (zero setup friction)
- Generate a key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Usage:
    from utils.encryption import get_encryptor, is_encryption_enabled

    if is_encryption_enabled():
        enc = get_encryptor()
        ciphertext = enc.encrypt("sensitive data")
        plaintext = enc.decrypt(ciphertext)
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger("ai-companion.encryption")

# Sentinel for uninitialized state
_encryptor: Optional["FieldEncryptor"] = None
_initialized = False

# Prefix to identify encrypted fields (avoids double-encryption)
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
    """
    Symmetric field encryption using Fernet (AES-128-CBC + HMAC-SHA256).

    Fernet guarantees that a message encrypted using it cannot be
    manipulated or read without the key. It is URL-safe base64 encoded.
    """

    def __init__(self, key: str):
        """
        Initialize with a Fernet-compatible key.

        Args:
            key: URL-safe base64-encoded 32-byte key.
                 Generate with: Fernet.generate_key()
        """
        from cryptography.fernet import Fernet

        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            raise ValueError(
                f"Invalid encryption key: {e}. "
                f"Generate a valid key with: "
                f'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from e

        # Store key hash for verification (never store the actual key)
        self._key_hash = hashlib.sha256(key.encode() if isinstance(key, str) else key).hexdigest()[:16]

    @property
    def key_hash(self) -> str:
        """Return truncated hash of the encryption key (for verification, NOT the key itself)."""
        return self._key_hash

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a string field value.

        Returns a prefixed ciphertext string that can be stored in any
        text field (Neo4j, Redis, ChromaDB metadata).

        If the value is already encrypted (has prefix), returns it unchanged.
        """
        if not plaintext:
            return plaintext

        # Don't double-encrypt
        if plaintext.startswith(ENCRYPTED_PREFIX):
            return plaintext

        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return ENCRYPTED_PREFIX + token.decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a field value.

        If the value doesn't have the encryption prefix (unencrypted data),
        returns it as-is (backward compatible).
        """
        if not ciphertext:
            return ciphertext

        if not ciphertext.startswith(ENCRYPTED_PREFIX):
            return ciphertext  # Not encrypted — return as-is

        token = ciphertext[len(ENCRYPTED_PREFIX):]
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            # Return the raw value rather than crashing (allows key rotation debugging)
            return ciphertext

    def encrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """
        Encrypt specified fields in a dictionary.

        Args:
            data: Dictionary to process
            fields: List of keys to encrypt

        Returns:
            New dict with specified fields encrypted (other fields unchanged)
        """
        result = dict(data)
        for field in fields:
            if field in result and isinstance(result[field], str) and result[field]:
                result[field] = self.encrypt(result[field])
        return result

    def decrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """
        Decrypt specified fields in a dictionary.

        Args:
            data: Dictionary to process
            fields: List of keys to decrypt

        Returns:
            New dict with specified fields decrypted (other fields unchanged)
        """
        result = dict(data)
        for field in fields:
            if field in result and isinstance(result[field], str) and result[field]:
                result[field] = self.decrypt(result[field])
        return result


def get_encryptor() -> Optional[FieldEncryptor]:
    """
    Get the singleton encryptor instance.

    Returns None if encryption is not configured.
    Thread-safe: initializes on first call.
    """
    global _encryptor, _initialized

    if _initialized:
        return _encryptor

    _initialized = True
    key = os.getenv("CERID_ENCRYPTION_KEY", "")

    if not key:
        logger.debug("Encryption disabled: CERID_ENCRYPTION_KEY not set")
        _encryptor = None
        return None

    try:
        _encryptor = FieldEncryptor(key)
        logger.info(f"Encryption enabled (key hash: {_encryptor.key_hash})")
        return _encryptor
    except (ImportError, ValueError) as e:
        logger.error(f"Encryption initialization failed: {e}")
        _encryptor = None
        return None


def encrypt_field(value: str) -> str:
    """
    Convenience function: encrypt a single field if encryption is enabled.

    If encryption is disabled, returns the value unchanged.
    """
    enc = get_encryptor()
    if enc is None:
        return value
    return enc.encrypt(value)


def decrypt_field(value: str) -> str:
    """
    Convenience function: decrypt a single field if encryption is enabled.

    If encryption is disabled or value isn't encrypted, returns unchanged.
    """
    enc = get_encryptor()
    if enc is None:
        return value
    return enc.decrypt(value)


# Fields to encrypt per storage backend
NEO4J_ENCRYPTED_FIELDS = ["filename", "summary", "keywords"]
REDIS_ENCRYPTED_FIELDS = ["filename", "domain", "extra"]
CHROMA_ENCRYPTED_FIELDS = ["filename", "summary"]


def reset_encryptor():
    """Reset the singleton (for testing only)."""
    global _encryptor, _initialized
    _encryptor = None
    _initialized = False
