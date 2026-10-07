"""Verificações da fundação do runtime MCP de dados."""

import pytest
from argos_mcp_data.server import StaticServiceTokenVerifier, create_mcp_server
from argos_mcp_data.settings import Settings


def complete_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "sql_dsn": "postgresql://readonly@localhost/argos",
        "mongo_uri": "mongodb://readonly@localhost:27017",
        "mongo_database": "argos",
        "internal_service_token": "internal-test-token",
        "mcp_auth_issuer_url": "https://issuer.example.test",
        "mcp_resource_server_url": "https://data.example.test/mcp",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_runtime_rejects_missing_read_only_connections_and_auth() -> None:
    with pytest.raises(ValueError, match="MCP_DATA_SQL_DSN"):
        create_mcp_server(Settings())


def test_mcp_server_initializes_with_auth_and_no_business_tools() -> None:
    server = create_mcp_server(complete_settings())

    assert server.name == "argos-mcp-data"
    assert server._tool_manager._tools == {}


def test_service_token_verifier_rejects_absent_or_wrong_token() -> None:
    verifier = StaticServiceTokenVerifier("expected", "https://data.example.test/mcp")

    assert not verifier.is_expected_token("")
    assert not verifier.is_expected_token("wrong")


def test_service_token_verifier_accepts_only_the_configured_token() -> None:
    verifier = StaticServiceTokenVerifier("expected", "https://data.example.test/mcp")

    assert verifier.is_expected_token("expected")


def test_mongo_reader_does_not_expose_write_methods() -> None:
    from argos_mcp_data.infrastructure.mongo import ReadOnlyMongoReader

    reader = ReadOnlyMongoReader(object())

    assert not hasattr(reader, "insert_one")
    assert not hasattr(reader, "update_one")
    assert not hasattr(reader, "delete_one")
