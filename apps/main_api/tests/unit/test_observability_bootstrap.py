"""Composição habilitada/desabilitada da coleta durante bootstrap do Mongo."""

import pytest
from app.infrastructure.bootstrap import _create_observability_runtime
from app.settings import Settings

from packages.observability import BestEffortObservationCollector, DisabledObservationCollector


def test_mongo_collection_names_are_fixed(monkeypatch):
    monkeypatch.setenv("MAIN_API_MONGO_COLLECTION_SESSIONS", "override_sessions")
    monkeypatch.setenv("MAIN_API_OBSERVABILITY_COLLECTION", "override_observations")

    settings = Settings.from_env()

    assert settings.mongo_collections.sessions == "ai_sessions"
    assert settings.observability_collection == "ai_observations"


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


class FakeWriter:
    def __init__(self, repository, **kwargs) -> None:
        self.repository = repository
        self.started = False

    async def start(self) -> None:
        self.started = True


@pytest.mark.asyncio
async def test_fixed_collection_starts_writer(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr("app.infrastructure.bootstrap.MongoObservationWriter", FakeWriter)

    writer, collector = await _create_observability_runtime(Settings(), database)
    assert writer is not None
    assert writer.started is True
    assert isinstance(collector, BestEffortObservationCollector)
    assert database.requested_collections == ["ai_observations"]


@pytest.mark.asyncio
async def test_mongo_failure_disables_observability_without_failing_bootstrap(caplog):
    database = FakeDatabase()

    writer, collector = await _create_observability_runtime(
        Settings(observability_collection="sensitive$collection"),
        database,
    )

    assert writer is None
    assert isinstance(collector, DisabledObservationCollector)
    assert "observability disabled" in caplog.text
    assert caplog.records[-1].component == "main_api_observability"
    assert "sensitive" not in caplog.text
    assert database.requested_collections == []
