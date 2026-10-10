"""Composição e ciclo de vida do A2A seguindo o runtime independente do Data."""

from dataclasses import dataclass
from typing import Any

from argos_a2a_agent.infrastructure import ReadOnlyQdrantReader
from argos_a2a_agent.server import create_a2a_server
from argos_a2a_agent.settings import Settings


@dataclass(slots=True)
class ServiceRuntime:
    qdrant_client: Any
    qdrant_reader: ReadOnlyQdrantReader
    server: Any

    async def close(self) -> None:
        await self.qdrant_client.close()


async def create_runtime(settings: Settings | None = None) -> ServiceRuntime:
    configured = settings or Settings.from_env()
    configured.validate_runtime()
    try:
        from qdrant_client import AsyncQdrantClient
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'qdrant-client' para iniciar o serviço") from exc

    client = AsyncQdrantClient(
        url=configured.qdrant_url,
        api_key=configured.qdrant_read_api_key,
        timeout=configured.qdrant_timeout_seconds,
    )
    try:
        await client.get_collections()
        reader = ReadOnlyQdrantReader(client, configured.qdrant_collection)
        server = create_a2a_server(configured, readiness_check=client.get_collections)
    except BaseException:
        await client.close()
        raise
    return ServiceRuntime(qdrant_client=client, qdrant_reader=reader, server=server)


async def run_server(settings: Settings | None = None) -> None:
    import uvicorn

    configured = settings or Settings.from_env()
    runtime = await create_runtime(configured)
    try:
        config = uvicorn.Config(runtime.server, host=configured.a2a_host, port=configured.a2a_port)
        await uvicorn.Server(config).serve()
    finally:
        await runtime.close()
