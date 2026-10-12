"""Configuração deve falhar antes de abrir recursos ou expor segredos."""

from dataclasses import replace

import pytest
from argos_mcp_prediction.settings import Settings

from packages.contracts import DataOrigin


@pytest.mark.parametrize(
    ("field", "env"),
    [
        ("delegation_issuer", "MCP_PREDICTION_DELEGATION_ISSUER"),
        ("sql_dsn", "MCP_PREDICTION_SQL_DSN"),
        ("internal_service_token", "MCP_PREDICTION_INTERNAL_SERVICE_TOKEN"),
        ("mcp_auth_issuer_url", "MCP_PREDICTION_AUTH_ISSUER_URL"),
        ("mcp_resource_server_url", "MCP_PREDICTION_RESOURCE_SERVER_URL"),
    ],
)
def test_missing_configuration_is_rejected(configured_settings, field, env):
    with pytest.raises(ValueError, match=env):
        replace(configured_settings, **{field: " "}).validate_runtime()


@pytest.mark.parametrize(
    "overrides",
    [
        {"trusted_delegation_keys": ()},
        {"delegation_max_ttl_seconds": 0},
        {"delegation_max_ttl_seconds": 301},
        {"delegation_clock_skew_seconds": -1},
        {"delegation_clock_skew_seconds": 61},
        {"sql_timeout_seconds": 0},
        {"sql_timeout_seconds": 61},
        {"mcp_port": 0},
        {"mcp_port": 65536},
    ],
)
def test_invalid_configuration_is_rejected(configured_settings, overrides):
    with pytest.raises(ValueError):
        replace(configured_settings, **overrides).validate_runtime()


def test_secrets_are_not_in_settings_representation(configured_settings):
    assert configured_settings.sql_dsn not in repr(configured_settings)
    assert configured_settings.internal_service_token not in repr(configured_settings)


def test_configuration_reads_only_prediction_environment(monkeypatch):
    monkeypatch.setenv("MCP_PREDICTION_SQL_DSN", "postgresql://readonly@localhost/test")
    monkeypatch.setenv("MCP_PREDICTION_INTERNAL_SERVICE_TOKEN", "own-token")
    monkeypatch.setenv("MCP_PREDICTION_AUTH_ISSUER_URL", "https://issuer.example.test")
    monkeypatch.setenv("MCP_PREDICTION_RESOURCE_SERVER_URL", "https://prediction.example.test/mcp")
    monkeypatch.setenv("MCP_PREDICTION_PORT", "8102")
    monkeypatch.setenv("MCP_PREDICTION_SQL_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("MCP_PREDICTION_DATA_ORIGIN", "synthetic")
    monkeypatch.setenv("MCP_EMAIL_INTERNAL_SERVICE_TOKEN", "other-token")
    monkeypatch.setenv("MCP_PREDICTION_DELEGATION_ISSUER", "argos-main-service")
    monkeypatch.setenv("MCP_PREDICTION_TRUSTED_DELEGATION_KEYS", '{"test-key": "public-key"}')
    settings = Settings.from_env()
    settings.validate_runtime()
    assert settings.internal_service_token == "own-token"
    assert settings.mcp_port == 8102
    assert settings.sql_timeout_seconds == 7
    assert settings.data_origin == DataOrigin.SYNTHETIC


def test_invalid_environment_integer_is_rejected(monkeypatch):
    monkeypatch.setenv("MCP_PREDICTION_PORT", "invalid")
    with pytest.raises(ValueError, match="MCP_PREDICTION_PORT"):
        Settings.from_env()


def test_invalid_data_origin_is_rejected(monkeypatch):
    monkeypatch.setenv("MCP_PREDICTION_DATA_ORIGIN", "unverified-value")
    with pytest.raises(ValueError, match="MCP_PREDICTION_DATA_ORIGIN"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["invalid", "[]", '{"key": 123}'])
def test_invalid_delegation_key_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setenv("MCP_PREDICTION_TRUSTED_DELEGATION_KEYS", value)
    with pytest.raises(ValueError, match="MCP_PREDICTION_TRUSTED_DELEGATION_KEYS"):
        Settings.from_env()
