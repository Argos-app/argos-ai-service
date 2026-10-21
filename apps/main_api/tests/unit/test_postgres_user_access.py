"""Contrato do leitor SQL, mapeamento cadastral e proteção do pool de leitura."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.infrastructure.postgres import create_postgres_user_access_runtime
from app.infrastructure.postgres_user_access import (
    PostgresUserAccessRepository,
    ReadOnlySqlReader,
)
from app.settings import Settings


def _record(**overrides):
    record = {
        "user_id": uuid4(),
        "firebase_uid": "firebase-sql-user",
        "user_name": "Nome do banco",
        "user_email": "database@example.test",
        "user_active": True,
        "blocked_until": None,
        "permission_code": "SISTEMA",
        "permission_name": "Sistema",
        "permission_active": True,
        "farm_id": uuid4(),
        "farm_name": "Fazenda",
    }
    record.update(overrides)
    return record


@pytest.mark.asyncio
async def test_repository_uses_parameterized_uid_and_maps_all_authorized_farms():
    first = _record()
    second = {**first, "farm_id": uuid4(), "farm_name": "Outra fazenda"}
    reader = SimpleNamespace(fetch_all=AsyncMock(return_value=(first, second)))
    uid = "uid' OR TRUE --"

    user = await PostgresUserAccessRepository(reader).find_by_firebase_uid(uid)

    query, *parameters = reader.fetch_all.call_args.args
    assert parameters == [uid]
    assert uid not in query
    assert "WHERE u.firebase_uid = $1" in query
    assert "u.email =" not in query
    assert user.user_id == first["user_id"]
    assert user.firebase_uid == first["firebase_uid"]
    assert user.name == first["user_name"]
    assert user.email == first["user_email"]
    assert user.permission_code == "SISTEMA"
    assert user.active is True and user.permission_active is True
    assert [(farm.farm_id, farm.name) for farm in user.farms] == [
        (first["farm_id"], first["farm_name"]),
        (second["farm_id"], second["farm_name"]),
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"user_active": False},
        {"permission_active": False},
        {"blocked_until": datetime.max.replace(tzinfo=UTC)},
    ],
)
@pytest.mark.asyncio
async def test_repository_preserves_cadastre_without_farms_for_authorization_decision(overrides):
    row = _record(farm_id=None, farm_name=None, **overrides)
    reader = SimpleNamespace(fetch_all=AsyncMock(return_value=(row,)))
    user = await PostgresUserAccessRepository(reader).find_by_firebase_uid(row["firebase_uid"])
    assert user is not None
    assert user.farms == ()
    assert user.active == row["user_active"]
    assert user.permission_active == row["permission_active"]
    assert user.blocked_until == row["blocked_until"]


@pytest.mark.asyncio
async def test_repository_returns_none_only_when_cadastre_is_absent():
    reader = SimpleNamespace(fetch_all=AsyncMock(return_value=()))
    assert await PostgresUserAccessRepository(reader).find_by_firebase_uid("unknown") is None


@pytest.mark.parametrize("uid", ["", "  "])
@pytest.mark.asyncio
async def test_repository_rejects_empty_uid_without_query(uid):
    reader = SimpleNamespace(fetch_all=AsyncMock())
    with pytest.raises(ValueError):
        await PostgresUserAccessRepository(reader).find_by_firebase_uid(uid)
    reader.fetch_all.assert_not_awaited()


def _pool():
    connection = MagicMock()
    connection.fetch = AsyncMock(return_value=({"field": "value"},))
    connection.fetchval = AsyncMock(side_effect=["on", True, False])
    connection.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    connection.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock()
    return pool, connection


@pytest.mark.asyncio
async def test_sql_reader_applies_timeout_read_only_transaction_and_bound_parameters():
    pool, connection = _pool()
    records = await ReadOnlySqlReader(pool, acquire_timeout_seconds=2).fetch_all(
        "SELECT $1", "firebase-uid"
    )
    pool.acquire.assert_called_once_with(timeout=2)
    connection.transaction.assert_called_once_with(readonly=True)
    connection.fetch.assert_awaited_once_with("SELECT $1", "firebase-uid")
    assert records == ({"field": "value"},)
    connection.transaction.return_value.__aexit__.assert_awaited_once()
    pool.acquire.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_sql_reader_propagates_failure_and_releases_resources():
    pool, connection = _pool()
    connection.fetch.side_effect = TimeoutError("query timeout")
    with pytest.raises(TimeoutError):
        await ReadOnlySqlReader(pool, acquire_timeout_seconds=2).fetch_all("SELECT $1", "uid")
    connection.transaction.return_value.__aexit__.assert_awaited_once()
    pool.acquire.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_without_dsn_does_not_create_pool(monkeypatch):
    create_pool = AsyncMock()
    monkeypatch.setattr("asyncpg.create_pool", create_pool)
    assert await create_postgres_user_access_runtime(Settings()) is None
    create_pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_sets_connection_limits_and_read_only_timeouts(monkeypatch):
    pool, _ = _pool()
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr("asyncpg.create_pool", create_pool)
    settings = Settings(auth_database_url="postgresql://test-only/db")
    runtime = await create_postgres_user_access_runtime(settings)
    arguments = create_pool.call_args
    assert arguments.args == (settings.auth_database_url,)
    assert arguments.kwargs["min_size"] == settings.auth_database_pool_min_size
    assert arguments.kwargs["max_size"] == settings.auth_database_pool_max_size
    assert arguments.kwargs["timeout"] == settings.auth_database_connect_timeout_seconds
    assert arguments.kwargs["command_timeout"] == settings.auth_database_query_timeout_seconds
    assert arguments.kwargs["server_settings"]["default_transaction_read_only"] == "on"
    assert arguments.kwargs["server_settings"]["statement_timeout"] == "3000ms"
    await runtime.close()
    pool.close.assert_awaited_once()


@pytest.mark.parametrize(
    "responses", [["off"], ["on", False, False], ["on", True, True], ["on", True, None]]
)
@pytest.mark.asyncio
async def test_runtime_rejects_unsafe_or_unusable_role_and_closes_pool(monkeypatch, responses):
    pool, connection = _pool()
    connection.fetchval.side_effect = responses
    monkeypatch.setattr("asyncpg.create_pool", AsyncMock(return_value=pool))
    with pytest.raises(RuntimeError):
        await create_postgres_user_access_runtime(
            Settings(auth_database_url="postgresql://test/db")
        )
    pool.close.assert_awaited_once()
