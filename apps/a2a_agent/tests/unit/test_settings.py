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


def test_injected_permission_adapter_does_not_require_sql_dsn():
    _runtime_settings().validate_runtime(
        require_providers=False,
        require_permission_sql=False,
    )


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
