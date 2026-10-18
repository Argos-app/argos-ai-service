"""Inicialização e encerramento dos recursos duráveis do main_api."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.infrastructure.checkpoints.mongo import (
    MongoCheckpointerRuntime,
    create_mongo_checkpointer,
)
from app.infrastructure.observability.mongo_repository import MongoObservationRepository
from app.infrastructure.observability.writer import MongoObservationWriter
from app.infrastructure.persistence.mongo import MongoPersistence
from app.settings import Settings
from packages.observability import (
    BestEffortObservationCollector,
    DisabledObservationCollector,
    ObservationCollector,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ServiceRuntime:
    mongo_client: Any
    database: Any
    persistence: MongoPersistence
    checkpoints: MongoCheckpointerRuntime
    observability_writer: MongoObservationWriter | None
    observation_collector: ObservationCollector

    @property
    def checkpointer(self) -> Any:
        return self.checkpoints.saver

    async def close(self) -> None:
        try:
            if self.observability_writer is not None:
                await self.observability_writer.close()
        finally:
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
        writer, collector = await _create_observability_runtime(settings, database)
        return ServiceRuntime(
            client,
            database,
            persistence,
            checkpoints,
            writer,
            collector,
        )
    except Exception:
        client.close()
        raise


async def _create_observability_runtime(
    settings: Settings,
    database: Any,
) -> tuple[MongoObservationWriter | None, ObservationCollector]:
    collection_name = settings.observability_collection
    if collection_name is None:
        return None, DisabledObservationCollector()
    try:
        if (
            not collection_name
            or collection_name.strip() != collection_name
            or collection_name.startswith("system.")
            or "$" in collection_name
            or "\x00" in collection_name
            or len(collection_name.encode("utf-8")) > 120
        ):
            raise ValueError("nome de coleção inválido")

        repository = MongoObservationRepository(database[collection_name])
        writer = MongoObservationWriter(
            repository,
            max_items=settings.observability_queue_max_items,
            max_bytes=settings.observability_queue_max_bytes,
            max_document_bytes=settings.observability_document_max_bytes,
            batch_size=settings.observability_batch_size,
            batch_interval_ms=settings.observability_batch_interval_ms,
            max_attempts=settings.observability_max_attempts,
            retry_base_delay_ms=settings.observability_retry_base_delay_ms,
            shutdown_timeout_ms=settings.observability_shutdown_timeout_ms,
        )
        await writer.start()
        return writer, BestEffortObservationCollector(writer)
    except Exception:
        _LOGGER.warning(
            "observability disabled because configuration could not be applied",
            extra={"setting": "MAIN_API_OBSERVABILITY_COLLECTION"},
        )
        return None, DisabledObservationCollector()
