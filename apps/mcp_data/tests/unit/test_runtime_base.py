"""Verificações da fundação do runtime MCP de dados."""

import pytest
from argos_mcp_data.application import CattleQueryService
from argos_mcp_data.server import (
    StaticServiceTokenVerifier,
    create_mcp_server,
    register_cattle_tools,
)
from argos_mcp_data.settings import Settings


def complete_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "sql_dsn": "postgresql://readonly@localhost/argos",
        "mongo_uri": "mongodb://readonly@localhost:27017",
        "mongo_database": "argos",
        "internal_service_token": "internal-test-token",
        "mcp_auth_issuer_url": "https://issuer.example.test",
        "mcp_resource_server_url": "https://data.example.test/mcp",
        "delegation_issuer": "argos-main-service",
        "trusted_delegation_keys": (("test-key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_runtime_rejects_missing_read_only_connections_and_auth() -> None:
    with pytest.raises(ValueError, match="MCP_DATA_SQL_DSN"):
        create_mcp_server(Settings())


def test_mcp_server_initializes_with_auth() -> None:
    server = create_mcp_server(complete_settings())

    assert server.name == "argos-mcp-data"
    assert server._tool_manager._tools == {}


def test_cattle_tools_are_registered_with_typed_payload_schemas() -> None:
    server = create_mcp_server(complete_settings())
    register_cattle_tools(server, CattleQueryService(object(), object()))

    tools = server._tool_manager._tools
    assert set(tools) == {
        "list_properties",
        "list_herds",
        "list_lots",
        "list_exception_animals",
        "list_applications",
        "list_application_animals",
        "list_occurrences",
        "list_animal_lot_history",
    }
    for tool in tools.values():
        schema = tool.parameters
        assert list(schema["properties"]) == ["payload"]
        payload = schema["properties"]["payload"]
        assert payload.get("$ref") or payload.get("type") == "object"
        definitions = schema.get("$defs", schema.get("definitions", {}))
        assert definitions
        assert all(value.get("additionalProperties") is False for value in definitions.values())


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
