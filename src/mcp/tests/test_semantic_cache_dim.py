"""Verify the configured embedding model default.

The HNSW-dim fixtures retired with the 2026-05-08 chromadb-backed
semantic_cache rewrite — chroma resolves the index dim from the first
upsert, so there is no module-level ``_HNSW_DIM`` to assert against.
The embedding-model default check stays here because the cache and the
KB collections both rely on it.
"""
import os


def test_embedding_model_default_is_arctic():
    """EMBEDDING_MODEL should default to Snowflake Arctic v1.5."""
    env_backup = os.environ.pop("EMBEDDING_MODEL", None)
    try:
        import importlib

        import config.settings as settings_mod
        importlib.reload(settings_mod)
        assert settings_mod.EMBEDDING_MODEL == "Snowflake/snowflake-arctic-embed-m-v1.5"
    finally:
        if env_backup is not None:
            os.environ["EMBEDDING_MODEL"] = env_backup
