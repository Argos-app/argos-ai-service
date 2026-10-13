"""Readiness do serviço A2A reflete a existência do runtime de agentes."""

from pathlib import Path

from argos_a2a_agent.server import create_a2a_server
from argos_a2a_agent.settings import Settings
from starlette.testclient import TestClient


def test_readiness_is_unavailable_without_a_configured_agent_registry():
    async def ready_check():
        return None

    settings = Settings(
        qdrant_url="http://localhost:6333",
        qdrant_read_api_key="read-key",
        qdrant_write_api_key="write-key",
        qdrant_collection="documents",
        qdrant_vector_size=3,
        document_source_root=Path.cwd(),
        manifest_path=Path.cwd() / "manifest.json",
        internal_service_token="service-token",
        resource_server_url="https://a2a.example/mcp",
        delegation_issuer="main-api",
        trusted_delegation_keys=(("test", "not-used-by-readiness"),),
        a2a_endpoint_url="https://a2a.example/a2a",
    )
    server = create_a2a_server(
        settings,
        readiness_check=ready_check,
        delegation_verifier=object(),
    )

    with TestClient(server) as client:
        response = client.get(
            "/health/ready",
            headers={"Authorization": "Bearer service-token"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "reason": "agent_runtime_not_configured",
    }
