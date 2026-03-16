"""Verify semantic cache dimension matches Arctic embedding model (768d)."""
import os


def test_semantic_cache_dim_default_is_768():
    """SEMANTIC_CACHE_DIM should default to 768 for Snowflake Arctic Embed M v1.5."""
    # Remove env var if set, to test the code default
    env_backup = os.environ.pop("SEMANTIC_CACHE_DIM", None)
    try:
        # Re-import to pick up the default
        import importlib
        import utils.semantic_cache as sc_mod
        importlib.reload(sc_mod)
        assert sc_mod._HNSW_DIM == 768, f"Expected 768, got {sc_mod._HNSW_DIM}"
    finally:
        if env_backup is not None:
            os.environ["SEMANTIC_CACHE_DIM"] = env_backup


def test_semantic_cache_dim_overridable_via_env(monkeypatch):
    """SEMANTIC_CACHE_DIM should be overridable via environment variable."""
    monkeypatch.setenv("SEMANTIC_CACHE_DIM", "256")
    import importlib
    import utils.semantic_cache as sc_mod
    importlib.reload(sc_mod)
    assert sc_mod._HNSW_DIM == 256


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
