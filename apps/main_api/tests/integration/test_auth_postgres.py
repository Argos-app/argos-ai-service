"""SELECT cadastral real em schema descartável de um PostgreSQL de testes."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from app.infrastructure.postgres_user_access import PostgresUserAccessRepository, ReadOnlySqlReader

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def auth_database():
    dsn = os.getenv("MAIN_API_TEST_AUTH_DATABASE_URL")
    if not dsn:
        pytest.skip("configure MAIN_API_TEST_AUTH_DATABASE_URL para um PostgreSQL descartável")
    import asyncpg

    schema = "auth_test_" + uuid4().hex
    connection = await asyncpg.connect(dsn, timeout=3)
    pool = None
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'SET search_path TO "{schema}"')
        await connection.execute("""
            CREATE TABLE permission (
                id uuid PRIMARY KEY, code text NOT NULL, name text NOT NULL,
                active boolean NOT NULL
            );
            CREATE TABLE app_user (
                id uuid PRIMARY KEY, firebase_uid text UNIQUE, name text NOT NULL,
                email text NOT NULL UNIQUE, active boolean NOT NULL, blocked_until timestamptz,
                permission_id uuid NOT NULL REFERENCES permission(id)
            );
            CREATE TABLE property (
                id uuid PRIMARY KEY, name text NOT NULL, active boolean NOT NULL,
                user_id uuid NOT NULL REFERENCES app_user(id)
            );
            CREATE TABLE user_property (
                user_id uuid NOT NULL REFERENCES app_user(id),
                property_id uuid NOT NULL REFERENCES property(id), active boolean NOT NULL,
                link_date date NOT NULL, unlink_date date
            );
        """)
        permission_id, user_id, other_id = uuid4(), uuid4(), uuid4()
        await connection.execute(
            "INSERT INTO permission VALUES ($1, 'SISTEMA', 'Sistema', TRUE)", permission_id
        )
        for key, uid, email in [
            (user_id, "firebase-user", "one@example.test"),
            (other_id, "firebase-other", "other@example.test"),
        ]:
            await connection.execute(
                "INSERT INTO app_user VALUES ($1, $2, 'Nome', $3, TRUE, NULL, $4)",
                key,
                uid,
                email,
                permission_id,
            )
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=1,
            timeout=3,
            command_timeout=3,
            server_settings={"search_path": schema, "default_transaction_read_only": "on"},
        )
        yield SimpleNamespace(
            sql=connection,
            user_id=user_id,
            other_id=other_id,
            permission_id=permission_id,
            repository=PostgresUserAccessRepository(
                ReadOnlySqlReader(pool, acquire_timeout_seconds=3)
            ),
        )
    finally:
        if pool is not None:
            await pool.close()
        try:
            await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await connection.close()


async def _property(db, owner, *, active=True):
    property_id = uuid4()
    await db.sql.execute(
        "INSERT INTO property VALUES ($1, 'Fazenda', $2, $3)", property_id, active, owner
    )
    return property_id


async def _link(db, property_id, *, active=True, start=-1, end=None):
    await db.sql.execute(
        """
        INSERT INTO user_property VALUES (
            $1, $2, $3, CURRENT_DATE + $4::integer,
            CASE WHEN $5::integer IS NULL THEN NULL ELSE CURRENT_DATE + $5::integer END
        )
    """,
        db.user_id,
        property_id,
        active,
        start,
        end,
    )


@pytest.mark.asyncio
async def test_postgres_isolates_users_and_never_matches_email_or_sql_input(auth_database):
    db = auth_database
    own = await _property(db, db.user_id)
    other = await _property(db, db.other_id)
    user = await db.repository.find_by_firebase_uid("firebase-user")
    second = await db.repository.find_by_firebase_uid("firebase-other")
    assert user.user_id == db.user_id
    assert {farm.farm_id for farm in user.farms} == {own}
    assert {farm.farm_id for farm in second.farms} == {other}
    assert await db.repository.find_by_firebase_uid("one@example.test") is None
    assert await db.repository.find_by_firebase_uid("' OR TRUE --") is None
    assert await db.repository.find_by_firebase_uid("not-linked") is None


@pytest.mark.parametrize(
    "active,start,end,allowed",
    [
        (True, -1, None, True),
        (True, 0, 0, True),
        (True, 1, None, False),
        (True, -2, -1, False),
        (False, -1, None, False),
    ],
)
@pytest.mark.asyncio
async def test_postgres_applies_link_status_and_inclusive_validity(
    auth_database, active, start, end, allowed
):
    db = auth_database
    farm = await _property(db, db.other_id)
    await _link(db, farm, active=active, start=start, end=end)
    user = await db.repository.find_by_firebase_uid("firebase-user")
    assert {f.farm_id for f in user.farms} == ({farm} if allowed else set())


@pytest.mark.parametrize("change", ["property", "user", "permission", "block"])
@pytest.mark.asyncio
async def test_postgres_reloads_access_after_cadastre_or_farm_is_disabled(auth_database, change):
    db = auth_database
    farm = await _property(db, db.user_id)
    assert (await db.repository.find_by_firebase_uid("firebase-user")).farms
    query, key = {
        "property": ("UPDATE property SET active = FALSE WHERE id = $1", farm),
        "user": ("UPDATE app_user SET active = FALSE WHERE id = $1", db.user_id),
        "permission": ("UPDATE permission SET active = FALSE WHERE id = $1", db.permission_id),
        "block": (
            "UPDATE app_user SET blocked_until = CURRENT_TIMESTAMP + INTERVAL '1 day' "
            "WHERE id = $1",
            db.user_id,
        ),
    }[change]
    await db.sql.execute(query, key)
    user = await db.repository.find_by_firebase_uid("firebase-user")
    assert user is not None
    assert user.farms == ()


@pytest.mark.asyncio
async def test_postgres_avoids_duplicate_farms_and_reloads_link_revocation(auth_database):
    db = auth_database
    farm = await _property(db, db.other_id)
    await _link(db, farm)
    await _link(db, farm)
    user = await db.repository.find_by_firebase_uid("firebase-user")
    assert [f.farm_id for f in user.farms] == [farm]
    await db.sql.execute("UPDATE user_property SET active = FALSE WHERE user_id = $1", db.user_id)
    assert (await db.repository.find_by_firebase_uid("firebase-user")).farms == ()
