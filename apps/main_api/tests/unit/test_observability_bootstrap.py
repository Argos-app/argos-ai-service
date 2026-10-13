"""Composição habilitada/desabilitada da coleta durante bootstrap do Mongo."""

import pytest
from app.infrastructure.bootstrap import _create_observability_runtime
from app.settings import Settings

from packages.observability import BestEffortObservationCollector, DisabledObservationCollector


class FakeCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], str]] = []

    async def create_index(self, keys, *, name: str):
        self.indexes.append((keys, name))
        return name


class FakeDatabase:
    def __init__(self) -> None:
        self.requested_collections: list[str] = []
        self.collection = FakeCollection()

    def __getitem__(self, name: str) -> FakeCollection:
        self.requested_collections.append(name)
        return self.collection


@pytest.mark.asyncio
async def test_missing_collection_configuration_disables_collection_without_mongo_access():
    database = FakeDatabase()

    writer, collector = await _create_observability_runtime(Settings(), database)

    assert writer is None
    assert isinstance(collector, DisabledObservationCollector)
    assert database.requested_collections == []


@pytest.mark.asyncio
async def test_configured_collection_starts_writer_and_collector():
    database = FakeDatabase()

    writer, collector = await _create_observability_runtime(
        Settings(observability_collection="raw_observations"),
        database,
    )
    try:
        assert writer is not None
        assert isinstance(collector, BestEffortObservationCollector)
        assert database.requested_collections == ["raw_observations"]
    finally:
        if writer is not None:
            await writer.close()

    assert {name for _, name in database.collection.indexes} == {
        "ix_observability_started_id",
        "ix_observability_updated_id",
    }


@pytest.mark.asyncio
async def test_invalid_collection_fails_open_without_logging_collection_value(caplog):
    database = FakeDatabase()

    writer, collector = await _create_observability_runtime(
        Settings(observability_collection="sensitive$collection"),
        database,
    )

    assert writer is None
    assert isinstance(collector, DisabledObservationCollector)
    assert "observability disabled" in caplog.text
    assert caplog.records[-1].setting == "MAIN_API_OBSERVABILITY_COLLECTION"
    assert "sensitive" not in caplog.text
    assert database.requested_collections == []
