"""Persistência idempotente e limites do writer bruto de observabilidade."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.infrastructure.observability.mongo_repository import (
    MongoObservationRepository,
    ObservationConflictError,
)
from app.infrastructure.observability.writer import MongoObservationWriter
from bson import BSON, Decimal128
from pymongo.errors import AutoReconnect, DuplicateKeyError

from packages.observability import (
    Billing,
    ErrorOrigin,
    ExecutionMode,
    Observation,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
    SubmissionResult,
    Usage,
    UsageAvailability,
)


class FakeCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.indexes: list[tuple[list[tuple[str, int]], str]] = []

    async def create_index(self, keys, *, name: str):
        self.indexes.append((keys, name))
        return name

    async def find_one(self, query):
        document = self.documents.get(query["_id"])
        return dict(document) if document is not None else None

    async def insert_one(self, document):
        if document["_id"] in self.documents:
            raise DuplicateKeyError("duplicate key", 11000)
        self.documents[document["_id"]] = dict(document)

    async def replace_one(self, query, document):
        current = self.documents.get(query["_id"])
        if current is None or current.get("status") != query.get("status"):
            return type("Result", (), {"matched_count": 0})()
        self.documents[query["_id"]] = dict(document)
        return type("Result", (), {"matched_count": 1})()


class FakeRepository:
    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.failures_remaining = 0

    def document_for(self, observation):
        return MongoObservationRepository.document_for(observation)

    async def ensure_indexes(self):
        return None

    async def write_document(self, document):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise AutoReconnect("connection details must not be logged")
        self.documents.append(dict(document))


def _observation(
    *,
    status: ObservationStatus = ObservationStatus.STARTED,
    operation_id: str | None = None,
    operation_name: str = "model.generate",
    session_id: UUID | None = None,
    interaction_id: UUID | None = None,
) -> Observation:
    started = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    terminal = status != ObservationStatus.STARTED
    ended = started + timedelta(milliseconds=37) if terminal else None
    return Observation(
        _id=operation_id or str(uuid4()),
        service="main_api",
        environment="test",
        operation_kind=OperationKind.MODEL,
        operation_name=operation_name,
        observation_level=ObservationLevel.SDK_INVOCATION,
        execution_mode=ExecutionMode.REAL,
        started_at=started,
        ended_at=ended,
        duration_ms=37.0 if terminal else None,
        updated_at=ended or started,
        status=status,
        session_id=session_id,
        interaction_id=interaction_id,
        error=(
            {
                "type": "timeout_error",
                "origin": ErrorOrigin.TRANSPORT,
                "code": "operation_timeout",
                "message_sanitized": "operation timed out",
            }
            if status == ObservationStatus.TIMEOUT
            else None
        ),
        usage=(Usage(availability=UsageAvailability.ABSENT) if terminal else None),
    )


def test_document_for_persists_session_and_interaction_join_keys():
    session_id, interaction_id = uuid4(), uuid4()
    document, _ = MongoObservationRepository.document_for(
        _observation(session_id=session_id, interaction_id=interaction_id)
    )

    assert document["schema_version"] == "2"
    assert document["session_id"] == str(session_id)
    assert document["interaction_id"] == str(interaction_id)


@pytest.mark.asyncio
async def test_repository_indexes_only_for_period_and_incremental_extraction():
    collection = FakeCollection()
    repository = MongoObservationRepository(collection)

    await repository.ensure_indexes()

    assert collection.indexes == [
        ([("correlation_id", 1), ("started_at", 1)], "ix_observability_correlation_started"),
        ([("interaction_id", 1), ("started_at", 1)], "ix_observability_interaction_started"),
        ([("session_id", 1), ("started_at", 1)], "ix_observability_session_started"),
        ([("started_at", 1), ("_id", 1)], "ix_observability_started_id"),
        ([("updated_at", 1), ("_id", 1)], "ix_observability_updated_id"),
    ]
    assert all("ttl" not in name.lower() for _, name in collection.indexes)


@pytest.mark.asyncio
async def test_terminal_replaces_start_and_duplicate_terminal_is_idempotent():
    collection = FakeCollection()
    repository = MongoObservationRepository(collection)
    started = _observation()
    terminal = _observation(
        status=ObservationStatus.SUCCEEDED,
        operation_id=started.id,
    )
    start_document, _ = repository.document_for(started)
    terminal_document, _ = repository.document_for(terminal)

    await repository.write_document(start_document)
    await repository.write_document(terminal_document)
    await repository.write_document(terminal_document)

    stored = collection.documents[started.id]
    assert stored["status"] == "succeeded"
    assert stored["started_at"] == started.started_at
    assert stored["ended_at"] == terminal.ended_at
    assert len(collection.documents) == 1


@pytest.mark.asyncio
async def test_terminal_is_inserted_complete_when_start_was_lost():
    collection = FakeCollection()
    repository = MongoObservationRepository(collection)
    terminal = _observation(status=ObservationStatus.SUCCEEDED)
    document, _ = repository.document_for(terminal)

    await repository.write_document(document)

    assert collection.documents[terminal.id]["status"] == "succeeded"
    assert collection.documents[terminal.id]["ended_at"] == terminal.ended_at


@pytest.mark.asyncio
async def test_replayed_start_does_not_regress_terminal_and_conflict_is_reported():
    collection = FakeCollection()
    repository = MongoObservationRepository(collection)
    started = _observation()
    terminal = _observation(status=ObservationStatus.SUCCEEDED, operation_id=started.id)
    await repository.write_document(repository.document_for(terminal)[0])

    await repository.write_document(repository.document_for(started)[0])
    with pytest.raises(ObservationConflictError):
        conflict = _observation(
            status=ObservationStatus.TIMEOUT,
            operation_id=started.id,
        )
        await repository.write_document(repository.document_for(conflict)[0])

    assert collection.documents[started.id]["status"] == "succeeded"


def test_bson_document_preserves_dates_decimal_and_size():
    observation = _observation(status=ObservationStatus.SUCCEEDED)
    correlation_id = uuid4()
    parent_observation_id = uuid4()
    observation = observation.model_copy(
        update={
            "billing": Billing(
                reported_cost=Decimal("0.0123"),
                currency="USD",
                cost_source="provider",
            ),
            "correlation_id": correlation_id,
            "parent_observation_id": parent_observation_id,
        }
    )

    document, size_bytes = MongoObservationRepository.document_for(observation)
    decoded = BSON.encode(document)

    assert size_bytes == len(decoded)
    decoded_document = BSON(decoded).decode()
    assert isinstance(decoded_document["started_at"], datetime)
    assert isinstance(decoded_document["billing"]["reported_cost"], Decimal128)
    assert decoded_document["correlation_id"] == str(correlation_id)
    assert decoded_document["parent_observation_id"] == str(parent_observation_id)
    assert "expires_at" not in document


def test_bson_document_persists_configured_rate_in_existing_billing_shape():
    observation = _observation(status=ObservationStatus.SUCCEEDED).model_copy(
        update={
            "billing": Billing(
                reported_cost=Decimal("0.001"),
                currency="USD",
                cost_source="configured_rate",
            )
        }
    )

    document, _ = MongoObservationRepository.document_for(observation)
    billing = document["billing"]

    assert set(billing) == {"reported_cost", "currency", "cost_source"}
    assert isinstance(billing["reported_cost"], Decimal128)
    assert billing["currency"] == "USD"
    assert billing["cost_source"] == "configured_rate"


@pytest.mark.asyncio
async def test_writer_limits_pending_items_and_drains_on_close():
    repository = FakeRepository()
    reports: list[str] = []
    writer = MongoObservationWriter(
        repository,
        max_items=1,
        max_bytes=16 * 1024,
        max_document_bytes=16 * 1024,
        batch_size=1,
        batch_interval_ms=250,
        max_attempts=1,
        shutdown_timeout_ms=1_000,
        report_failure=reports.append,
    )
    await writer.start()

    assert writer.try_submit(_observation()) == SubmissionResult.ACCEPTED
    assert writer.try_submit(_observation()) == SubmissionResult.QUEUE_FULL
    await writer.close()

    assert len(repository.documents) == 1
    assert reports == []
    assert writer.try_submit(_observation()) == SubmissionResult.NOT_RUNNING


@pytest.mark.asyncio
async def test_writer_flushes_each_observation_once_in_multi_item_batch():
    repository = FakeRepository()
    writer = MongoObservationWriter(
        repository,
        max_items=10,
        max_bytes=64 * 1024,
        max_document_bytes=16 * 1024,
        batch_size=10,
        batch_interval_ms=250,
        max_attempts=1,
    )
    started = _observation()
    terminal = _observation(status=ObservationStatus.SUCCEEDED, operation_id=started.id)
    independent = _observation(status=ObservationStatus.SUCCEEDED)
    await writer.start()
    for observation in (started, terminal, independent):
        assert writer.try_submit(observation) == SubmissionResult.ACCEPTED
    await writer.close()

    assert len(repository.documents) == 3
    assert [
        document["status"] for document in repository.documents if document["_id"] == started.id
    ] == ["started", "succeeded"]


@pytest.mark.asyncio
async def test_writer_limits_queued_bytes_per_process():
    repository = FakeRepository()
    observation = _observation()
    _, document_size = MongoObservationRepository.document_for(observation)
    writer = MongoObservationWriter(
        repository,
        max_items=10,
        max_bytes=document_size,
        max_document_bytes=document_size,
        batch_size=10,
        batch_interval_ms=250,
        max_attempts=1,
    )
    await writer.start()

    assert writer.try_submit(observation) == SubmissionResult.ACCEPTED
    assert writer.try_submit(_observation()) == SubmissionResult.QUEUE_FULL
    await writer.close()

    assert len(repository.documents) == 1


@pytest.mark.asyncio
async def test_writer_retries_transient_failures_and_releases_queue_capacity():
    repository = FakeRepository()
    repository.failures_remaining = 2
    writer = MongoObservationWriter(
        repository,
        max_items=1,
        max_bytes=16 * 1024,
        max_document_bytes=16 * 1024,
        batch_size=1,
        batch_interval_ms=1,
        max_attempts=3,
        retry_base_delay_ms=0,
    )
    await writer.start()

    assert writer.try_submit(_observation()) == SubmissionResult.ACCEPTED
    for _ in range(100):
        if repository.documents:
            break
        await asyncio.sleep(0.01)
    assert len(repository.documents) == 1
    assert writer.try_submit(_observation()) == SubmissionResult.ACCEPTED
    await writer.close()

    assert len(repository.documents) == 2


@pytest.mark.asyncio
async def test_writer_drops_permanent_mongo_failure_and_keeps_close_available():
    repository = FakeRepository()
    reports: list[str] = []

    async def permanent_failure(_document):
        raise PermissionError("sensitive mongo URI must not be logged")

    repository.write_document = permanent_failure
    writer = MongoObservationWriter(
        repository,
        max_items=1,
        max_bytes=16 * 1024,
        max_document_bytes=16 * 1024,
        batch_size=1,
        batch_interval_ms=1,
        max_attempts=3,
        report_failure=reports.append,
    )
    await writer.start()
    assert writer.try_submit(_observation()) == SubmissionResult.ACCEPTED

    for _ in range(100):
        if reports:
            break
        await asyncio.sleep(0.01)
    assert writer.try_submit(_observation()) == SubmissionResult.ACCEPTED
    await writer.close()

    assert reports == ["mongo_write_failed"]


@pytest.mark.asyncio
async def test_writer_shutdown_is_bounded_when_mongo_write_stalls():
    entered_write = asyncio.Event()
    reports: list[str] = []
    repository = FakeRepository()

    async def stalled_write(_document):
        entered_write.set()
        await asyncio.Event().wait()

    repository.write_document = stalled_write
    writer = MongoObservationWriter(
        repository,
        max_items=1,
        max_bytes=16 * 1024,
        max_document_bytes=16 * 1024,
        batch_size=1,
        batch_interval_ms=1,
        max_attempts=1,
        shutdown_timeout_ms=20,
        report_failure=reports.append,
    )
    await writer.start()
    assert writer.try_submit(_observation()) == SubmissionResult.ACCEPTED
    await asyncio.wait_for(entered_write.wait(), timeout=1)

    await asyncio.wait_for(writer.close(), timeout=0.5)

    assert reports == ["shutdown_pending"]
    assert writer.try_submit(_observation()) == SubmissionResult.NOT_RUNNING
