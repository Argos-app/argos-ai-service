"""Exercita a seleção existente com dados descartáveis, sem banco de negócio.

SQLite valida joins e predicados desta consulta; PostgreSQL real fica na etapa 10.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_prediction.infrastructure.access_directory import PostgresAccessDirectory

pytestmark = pytest.mark.asyncio


class FixtureSqlReader:
    def __init__(self, database):
        self.database = database

    async def fetch_all(self, query, *parameters):
        bindings = {str(index): str(value) for index, value in enumerate(parameters, start=1)}
        return self.database.execute(query, bindings).fetchall()


@pytest.mark.parametrize(
    "change",
    [
        None,
        "user_inactive",
        "permission_inactive",
        "blocked",
        "property_inactive",
        "link_inactive",
        "future_link",
        "expired_link",
    ],
)
async def test_current_user_and_link_must_be_active(change):
    user, owner, property_id = uuid4(), uuid4(), uuid4()
    with sqlite3.connect(":memory:") as database:
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE permission (id TEXT, active BOOLEAN);
            CREATE TABLE app_user (id TEXT, permission_id TEXT, active BOOLEAN, blocked_until TEXT);
            CREATE TABLE property (id TEXT, user_id TEXT, active BOOLEAN);
            CREATE TABLE user_property (user_id TEXT, property_id TEXT, active BOOLEAN,
                                        link_date TEXT, unlink_date TEXT);
        """)
        database.execute("INSERT INTO permission VALUES ('permission', 1)")
        database.execute("INSERT INTO app_user VALUES (?, 'permission', 1, NULL)", (str(user),))
        database.execute("INSERT INTO property VALUES (?, ?, 1)", (str(property_id), str(owner)))
        database.execute(
            "INSERT INTO user_property VALUES (?, ?, 1, '2000-01-01', NULL)",
            (str(user), str(property_id)),
        )
        updates = {
            "user_inactive": "UPDATE app_user SET active = 0",
            "permission_inactive": "UPDATE permission SET active = 0",
            "property_inactive": "UPDATE property SET active = 0",
            "link_inactive": "UPDATE user_property SET active = 0",
            "future_link": "UPDATE user_property SET link_date = '2999-01-01'",
            "expired_link": "UPDATE user_property SET unlink_date = '2000-01-01'",
        }
        if change == "blocked":
            future = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            database.execute("UPDATE app_user SET blocked_until = ?", (future,))
        elif change:
            database.execute(updates[change])
        directory = PostgresAccessDirectory(FixtureSqlReader(database))
        assert await directory.get_active_property_ids(user) == (
            {property_id} if change is None else set()
        )
        assert await directory.get_active_property_ids(uuid4()) == set()
        # Ownership remains valid without a link, except for inactive/blocked entities.
        database.execute("UPDATE property SET user_id = ?", (str(user),))
        if change in (None, "link_inactive", "future_link", "expired_link"):
            assert await directory.get_active_property_ids(user) == {property_id}
