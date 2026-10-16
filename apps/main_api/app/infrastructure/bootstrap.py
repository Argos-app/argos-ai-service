"""Inicialização e encerramento dos recursos duráveis do main_api."""

import asyncio
from dataclasses import dataclass
from typing import Any

from app.infrastructure.checkpoints.mongo import (
    MongoCheckpointerRuntime,
    create_mongo_checkpointer,
)
from app.infrastructure.persistence.mongo import MongoPersistence
from app.settings import Settings


@dataclass(slots=True)
class ServiceRuntime:
    mongo_client: Any
    database: Any
    persistence: MongoPersistence
    checkpoints: MongoCheckpointerRuntime

    @property
    def checkpointer(self) -> Any:
        return self.checkpoints.saver

    async def close(self) -> None:
        try:
            self.checkpoints.close()
        finally:
            self.mongo_client.close()


async def create_runtime(settings: Settings) -> ServiceRuntime:
    """Conecta ao replica set, aplica write concern e prepara índices."""
    if not settings.mongo_uri:
        raise ValueError("MAIN_API_MONGO_URI é obrigatório para criar o runtime")
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        from pymongo.write_concern import WriteConcern
    except ImportError as exc:
        raise RuntimeError("instale a dependência motor para usar persistência Mongo") from exc

    client = AsyncIOMotorClient(
        settings.mongo_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=settings.mongo_server_selection_timeout_ms,
    )
    try:
        await client.admin.command("ping")
        topology = await client.admin.command("hello")
        if not topology.get("setName"):
            raise RuntimeError("Mongo de sessões deve operar como replica set")
        database = client.get_database(
            settings.mongo_database,
            write_concern=WriteConcern(w="majority", wtimeout=settings.mongo_write_timeout_ms),
        )
        persistence = MongoPersistence(database)
        await persistence.ensure_indexes()
        checkpoints = await asyncio.to_thread(create_mongo_checkpointer, settings)
        return ServiceRuntime(client, database, persistence, checkpoints)
    except Exception:
        client.close()
        raise
