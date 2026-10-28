from dataclasses import replace
from pathlib import Path

import pytest
from argos_a2a_agent.settings import Settings


def _runtime_settings() -> Settings:
    return Settings(
        qdrant_url="http://127.0.0.1:6333",
        qdrant_read_api_key="read-key",
        qdrant_write_api_key="write-key",
        qdrant_collection="documents",
        qdrant_vector_size=3,
        document_source_root=Path.cwd(),
        manifest_path=Path.cwd() / "manifest.json",
        internal_service_token="service-token",
        resource_server_url="http://127.0.0.1:8000/a2a",
        delegation_issuer="argos-test",
        trusted_delegation_keys=(("key-id", "test-public-key"),),
        a2a_endpoint_url="http://127.0.0.1:8000/a2a",
        model_mode="simulated",
    )


def test_observability_collection_name_is_fixed(monkeypatch):
    monkeypatch.setenv("A2A_OBSERVABILITY_COLLECTION", "override_observations")

    assert Settings.from_env().observability_collection == "ai_a2a_observations"


def test_openai_models_are_loaded_from_shared_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL_SPECIALIST", "specialist-test")
    monkeypatch.setenv("OPENAI_EMBEDDINGS_MODEL", "embedding-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-gateway.example/v1/")
    monkeypatch.setenv("OPENAI_EMBEDDINGS_URL", "https://openai-gateway.example/embed/")

    settings = Settings.from_env()

    assert settings.openai_api_key == "test-key"
    assert settings.model_specialist_name == "specialist-test"
    assert settings.embeddings_model == "embedding-test"
    assert settings.openai_base_url == "https://openai-gateway.example/v1"
    assert settings.openai_embeddings_url == "https://openai-gateway.example/embed"


def test_provider_urls_have_no_built_in_defaults(monkeypatch):
    for name in (
        "OPENAI_BASE_URL",
        "OPENAI_EMBEDDINGS_URL",
        "ANTHROPIC_BASE_URL",
        "VOYAGE_EMBEDDINGS_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.openai_base_url is None
    assert settings.openai_embeddings_url is None
    assert settings.anthropic_base_url is None
    assert settings.voyage_embeddings_url is None


def test_fallback_models_have_no_built_in_names(monkeypatch):
    monkeypatch.delenv("VOYAGE_EMBEDDINGS_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL_SPECIALIST_FALLBACK", raising=False)

    settings = Settings.from_env()

    assert settings.voyage_embeddings_model is None
    assert settings.anthropic_model_specialist_fallback is None


def test_voyage_embedding_settings_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-test-key")
    monkeypatch.setenv("VOYAGE_EMBEDDINGS_MODEL", "voyage-4-large")
    monkeypatch.setenv("A2A_QDRANT_VOYAGE_VECTOR_SIZE", "1024")
    monkeypatch.setenv("VOYAGE_EMBEDDINGS_URL", "https://voyage-gateway.example/embed/")

    settings = Settings.from_env()

    assert settings.voyage_api_key == "voyage-test-key"
    assert settings.voyage_embeddings_model == "voyage-4-large"
    assert settings.qdrant_voyage_vector_size == 1024
    assert settings.voyage_embeddings_url == "https://voyage-gateway.example/embed"


def test_provider_mode_requires_voyage_credentials_for_embedding_fallback():
    settings = replace(
        _runtime_settings(),
        model_mode="provider",
        openai_api_key="openai-test-key",
        openai_base_url="https://openai.example/v1",
        openai_embeddings_url="https://openai.example/v1/embeddings",
        embeddings_model="text-embedding-3-small",
        model_specialist_name="gpt-6-luna",
        voyage_embeddings_url="https://voyage.example/v1/embeddings",
        permission_sql_dsn="postgresql://reader:test@localhost/argos",
    )

    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        settings.validate_runtime(require_permission_sql=False)


def test_anthropic_fallback_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic-gateway.example/")
    monkeypatch.setenv("ANTHROPIC_MODEL_SPECIALIST_FALLBACK", "sonnet-test")

    settings = Settings.from_env()

    assert settings.anthropic_api_key == "anthropic-test-key"
    assert settings.anthropic_base_url == "https://anthropic-gateway.example"
    assert settings.anthropic_model_specialist_fallback == "sonnet-test"


def test_injected_permission_adapter_does_not_require_sql_dsn():
    _runtime_settings().validate_runtime(
        require_providers=False,
        require_permission_sql=False,
    )


def test_provider_endpoint_urls_must_be_http_urls():
    settings = replace(_runtime_settings(), openai_embeddings_url="file:///embeddings")

    with pytest.raises(ValueError, match="OPENAI_EMBEDDINGS_URL"):
        settings.validate_runtime(require_providers=False, require_permission_sql=False)


def test_provider_mode_requires_all_model_and_embedding_endpoints():
    settings = replace(
        _runtime_settings(),
        model_mode="provider",
        openai_api_key="openai-key",
        embeddings_model="openai-embed",
        voyage_api_key="voyage-key",
        voyage_embeddings_model="voyage-embed",
        model_specialist_name="openai-model",
        permission_sql_dsn="postgresql://reader:test@localhost/argos",
    )

    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        settings.validate_runtime(require_permission_sql=False)


def test_production_runtime_still_requires_permission_sql_dsn():
    with pytest.raises(ValueError, match="A2A_PERMISSION_SQL_DSN"):
        _runtime_settings().validate_runtime(require_providers=False)


def test_a2a_model_timeout_allows_longer_provider_responses():
    settings = _runtime_settings()

    assert settings.model_timeout_seconds == 40
    settings.validate_runtime(require_providers=False, require_permission_sql=False)


def test_a2a_model_timeout_remains_bounded():
    settings = replace(_runtime_settings(), model_timeout_seconds=46)

    with pytest.raises(ValueError, match="A2A_MODEL_TIMEOUT_SECONDS"):
        settings.validate_runtime(require_providers=False, require_permission_sql=False)
