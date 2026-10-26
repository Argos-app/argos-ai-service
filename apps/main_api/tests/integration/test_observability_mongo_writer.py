"""Verifica a persistência bruta com MongoDB descartável em replica set."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.infrastructure.observability.mongo_repository import MongoObservationRepository
from app.infrastructure.observability.writer import MongoObservationWriter
from motor.motor_asyncio import AsyncIOMotorClient

from packages.observability import (
    ExecutionMode,
    Observation,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mongo_writer_persists_one_attempt_and_updates_it_idempotently():
    mongo_uri = os.getenv("MAIN_API_MONGO_TEST_URI")
    if not mongo_uri:
        pytest.skip("defina MAIN_API_MONGO_TEST_URI para executar a integração Mongo")

    database_name = f"argos_observability_test_{uuid4().hex}"
    client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    database = client[database_name]
    collection = database["raw_observations"]
    now = datetime.now(UTC).replace(microsecond=0)
    operation_id = str(uuid4())
    started = Observation(
        _id=operation_id,
        service="main_api",
        environment="test",
        operation_kind=OperationKind.MODEL,
        operation_name="model.generate",
        observation_level=ObservationLevel.SDK_INVOCATION,
        execution_mode=ExecutionMode.REAL,
        started_at=now,
        updated_at=now,
        status=ObservationStatus.STARTED,
    )
    finished_at = now + timedelta(milliseconds=25)
    terminal = started.model_copy(
        update={
            "ended_at": finished_at,
            "duration_ms": 25.0,
            "updated_at": finished_at,
            "status": ObservationStatus.SUCCEEDED,
        }
    )
    writer = MongoObservationWriter(
        MongoObservationRepository(collection),
        batch_size=1,
        batch_interval_ms=1,
    )

    try:
        await writer.start()
        assert writer.try_submit(started).value == "accepted"
        await writer.close()

        # Reopen a writer against the same repository to exercise terminal update
        # and duplicate delivery across separate in-memory batches.
        writer = MongoObservationWriter(
            MongoObservationRepository(collection),
            batch_size=1,
            batch_interval_ms=1,
        )
        await writer.start()
        assert writer.try_submit(terminal).value == "accepted"
        await writer.close()
        assert writer.try_submit(terminal).value == "writer_not_running"

        stored = await collection.find_one({"_id": operation_id})
        assert stored is not None
        assert stored["status"] == "succeeded"
        assert stored["started_at"] == now
        assert stored["ended_at"] == finished_at
        assert stored["duration_ms"] == 25.0
        assert await collection.count_documents({}) == 1
        indexes = await collection.index_information()
        assert "ix_observability_correlation_started" in indexes
        assert "ix_observability_interaction_started" in indexes
        assert "ix_observability_session_started" in indexes
        assert "ix_observability_started_id" in indexes
        assert "ix_observability_updated_id" in indexes
        assert all("expireAfterSeconds" not in index for index in indexes.values())
    finally:
        await client.drop_database(database_name)
        client.close()
