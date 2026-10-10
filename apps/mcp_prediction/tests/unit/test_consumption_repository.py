"""Agregação real em fixture SQL local e validação da fronteira do leitor.

SQLite adapta arrays, casts e extração do mês; PostgreSQL real fica na etapa 10.
Os valores da fixture SQL são inteiros exatos. Precisão Decimal usa o leitor falso.
"""

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from argos_mcp_prediction.domain import ConsumptionUnit
from argos_mcp_prediction.domain.errors import (
    ConsumptionResultLimitError,
    ConsumptionSourceUnavailableError,
    InvalidConsumptionDataError,
)
from argos_mcp_prediction.infrastructure import PostgresConsumptionRepository

from packages.security.access import AccessContext, AuthorizedScope
from packages.security.errors import AccessDeniedError

pytestmark = pytest.mark.asyncio
START, END = date(2024, 1, 15), date(2025, 1, 14)


def make_scope(properties):
    now = datetime.now(UTC)
    context = AccessContext(
        uuid4(),
        properties,
        frozenset({"prediction.read"}),
        "analista",
        now,
        now + timedelta(seconds=120),
        "token",
        "request",
    )
    return AuthorizedScope.build(
        context, current_property_ids=set(properties), operation="prediction.read"
    )


class FixtureSqlReader:
    def __init__(self, database):
        self.database = database

    async def fetch_all(self, query, *parameters):
        query = query.replace("p.id = ANY($1::uuid[])", "p.id IN (SELECT value FROM json_each($1))")
        query = query.replace(
            "FROM unnest($4::text[]) AS requested(name)", "FROM json_each($4) AS requested"
        )
        query = query.replace(
            "position(lower(requested.name) in lower(m.brand_name))",
            "instr(lower(m.brand_name), lower(requested.value))",
        )
        query = query.replace(
            "position(lower(requested.name) in lower(m.active_ingredient))",
            "instr(lower(m.active_ingredient), lower(requested.value))",
        )
        query = query.replace(
            "EXTRACT(MONTH FROM a.application_date)",
            "CAST(strftime('%m', a.application_date) AS INTEGER)",
        )
        for cast in ("::uuid[]", "::text[]", "::date", "::integer", "::bigint"):
            query = query.replace(cast, "")
        properties, start, end, names, limit = parameters
        rows = self.database.execute(
            query,
            {
                "1": json.dumps([str(value) for value in properties]),
                "2": start.isoformat(),
                "3": end.isoformat(),
                "4": None if names is None else json.dumps(list(names)),
                "5": limit,
            },
        ).fetchall()
        return tuple(
            {
                **dict(row),
                "quantity": Decimal(str(row["quantity"])),
                "any_inconsistent": bool(row["any_inconsistent"]),
            }
            for row in rows
        )


@pytest.fixture
def fixture_database():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript("""
        CREATE TABLE property (id TEXT PRIMARY KEY, active BOOLEAN);
        CREATE TABLE herd (id TEXT PRIMARY KEY, property_id TEXT, active BOOLEAN);
        CREATE TABLE lot (id TEXT PRIMARY KEY, herd_id TEXT, active BOOLEAN, close_date TEXT);
        CREATE TABLE medicine (
            id TEXT PRIMARY KEY, active BOOLEAN, brand_name TEXT, active_ingredient TEXT);
        CREATE TABLE medicine_batch (id TEXT PRIMARY KEY, medicine_id TEXT);
        CREATE TABLE application (id TEXT PRIMARY KEY, lot_id TEXT, medicine_batch_id TEXT,
            application_date TEXT, unit TEXT, dose INTEGER, head_count_applied INTEGER,
            active BOOLEAN);
        CREATE TABLE application_animal (
            id TEXT PRIMARY KEY, application_id TEXT, dose_given INTEGER);
    """)
    a, b, medicine, other_medicine = uuid4(), uuid4(), uuid4(), uuid4()
    for property_id in (a, b):
        database.execute("INSERT INTO property VALUES (?, 1)", (str(property_id),))
        database.execute("INSERT INTO herd VALUES (?, ?, 0)", (str(property_id), str(property_id)))
        database.execute(
            "INSERT INTO lot VALUES (?, ?, 0, '2025-01-14')", (str(property_id), str(property_id))
        )
    for identifier, name, ingredient in (
        (medicine, "Brand One", "Compound One"),
        (other_medicine, "Brand Two", "Compound Two"),
    ):
        database.execute(
            "INSERT INTO medicine VALUES (?, 0, ?, ?)", (str(identifier), name, ingredient)
        )
        database.execute(
            "INSERT INTO medicine_batch VALUES (?, ?)", (str(identifier), str(identifier))
        )

    def add(property_id, medicine_id, observed_on, dose, heads, unit="ml", active=True, details=()):
        application_id = str(uuid4())
        database.execute(
            "INSERT INTO application VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                application_id,
                str(property_id),
                str(medicine_id),
                observed_on,
                unit,
                dose,
                heads,
                active,
            ),
        )
        for given in details:
            database.execute(
                "INSERT INTO application_animal VALUES (?, ?, ?)",
                (str(uuid4()), application_id, given),
            )
        return application_id

    add(a, medicine, "2024-01-15", 2, 3)
    add(a, medicine, "2025-01-14", 5, 2)
    individual = add(a, medicine, "2024-02-01", 3, 1, details=(3,))
    collective = add(a, medicine, "2024-02-02", 2, 2, details=(2, 2))
    add(a, medicine, "2024-03-01", 4, 2, unit="mg")
    add(a, other_medicine, "2024-05-01", 1, 1, unit="dose")
    add(b, medicine, "2024-01-15", 999, 999, details=(1,))
    add(a, medicine, "2024-01-14", 999, 999)
    add(a, medicine, "2025-01-15", 999, 999)
    add(a, medicine, "2024-01-15", 999, 999, active=False, details=(1,))
    try:
        yield database, a, b, medicine, other_medicine, individual, collective, add
    finally:
        database.close()


async def test_sql_aggregates_all_scoped_base_applications_once(fixture_database):
    database, a, _, medicine, other, *_ = fixture_database
    repository = PostgresConsumptionRepository(FixtureSqlReader(database))
    rows = await repository.aggregate_monthly(make_scope((a,)), start=START, end=END)
    result = {(row.group.medicine_id, row.group.unit, row.month): row for row in rows}
    january = result[medicine, ConsumptionUnit.ML, 1]
    february = result[medicine, ConsumptionUnit.ML, 2]
    assert (january.quantity, january.application_events, january.head_administrations) == (
        16,
        2,
        5,
    )
    assert (february.quantity, february.application_events, february.head_administrations) == (
        7,
        2,
        3,
    )
    assert result[medicine, ConsumptionUnit.MG, 3].quantity == 8
    assert result[other, ConsumptionUnit.DOSE, 5].quantity == 1
    assert len(rows) == 4
    assert all(row.group.property_id == a for row in rows)
    selected = await repository.aggregate_monthly(
        make_scope((a,)), start=START, end=END, medicine_names=("brand one",)
    )
    assert len(selected) == 3
    missing = await repository.aggregate_monthly(
        make_scope((a,)), start=START, end=END, medicine_names=("not a medicine",)
    )
    assert missing == ()


@pytest.mark.parametrize(
    "change", ["different_dose", "extra_detail", "missing_detail", "balanced_doses"]
)
async def test_individual_inconsistency_rejects_whole_result(fixture_database, change):
    database, a, _, _, _, individual, collective, _ = fixture_database
    if change == "different_dose":
        database.execute(
            "UPDATE application_animal SET dose_given = 4 WHERE application_id = ?", (individual,)
        )
    elif change == "extra_detail":
        database.execute(
            "INSERT INTO application_animal VALUES (?, ?, 3)", (str(uuid4()), individual)
        )
    elif change == "missing_detail":
        database.execute(
            "DELETE FROM application_animal WHERE id = "
            "(SELECT id FROM application_animal WHERE application_id = ? LIMIT 1)",
            (collective,),
        )
    else:
        ids = database.execute(
            "SELECT id FROM application_animal WHERE application_id = ?", (collective,)
        ).fetchall()
        for row, dose in zip(ids, (1, 3), strict=True):
            database.execute(
                "UPDATE application_animal SET dose_given = ? WHERE id = ?", (dose, row[0])
            )
    with pytest.raises(InvalidConsumptionDataError):
        await PostgresConsumptionRepository(FixtureSqlReader(database)).aggregate_monthly(
            make_scope((a,)), start=START, end=END
        )


@pytest.mark.parametrize("count", [1000, 1001])
async def test_group_limit_never_returns_truncated_estimate(fixture_database, count):
    database, a, _, medicine, *_ = fixture_database
    database.execute("DELETE FROM application_animal")
    database.execute("DELETE FROM application")
    add = fixture_database[-1]
    for _ in range(count):
        identifier = uuid4()
        database.execute(
            "INSERT INTO medicine VALUES (?, 1, ?, ?)",
            (str(identifier), f"Brand {identifier}", f"Compound {identifier}"),
        )
        database.execute(
            "INSERT INTO medicine_batch VALUES (?, ?)", (str(identifier), str(identifier))
        )
        add(a, identifier, "2024-01-15", 1, 1)
    repository = PostgresConsumptionRepository(FixtureSqlReader(database))
    if count == 1001:
        with pytest.raises(ConsumptionResultLimitError):
            await repository.aggregate_monthly(make_scope((a,)), start=START, end=END)
    else:
        assert (
            len(await repository.aggregate_monthly(make_scope((a,)), start=START, end=END)) == 1000
        )


def aggregate_row(scope, medicine_id):
    return {
        "property_id": scope.property_ids[0],
        "medicine_id": medicine_id,
        "medicine_name": "Test brand",
        "active_ingredient": "Test compound",
        "unit": "ml",
        "month": 1,
        "quantity": Decimal("0.1234567890123456789"),
        "application_events": 1,
        "head_administrations": 2,
        "group_count": 1,
        "any_inconsistent": False,
    }


async def test_query_parameters_and_decimal_precision_are_preserved():
    scope, medicine = make_scope((uuid4(),)), uuid4()
    reader = AsyncMock()
    reader.fetch_all.return_value = [aggregate_row(scope, medicine)]
    result = await PostgresConsumptionRepository(reader).aggregate_monthly(
        scope, start=START, end=END, medicine_names=("requested brand",)
    )
    query, *parameters = reader.fetch_all.await_args.args
    assert parameters == [list(scope.property_ids), START, END, ["requested brand"], 12001]
    assert str(medicine) not in query and str(scope.property_ids[0]) not in query
    assert result[0].quantity == Decimal("0.1234567890123456789")


@pytest.mark.parametrize("change", ["absent", "expired", "wrong_agent", "wrong_operation", "empty"])
async def test_invalid_scope_never_queries_source(change):
    scope = make_scope((uuid4(),))
    if change == "absent":
        scope = None
    elif change == "expired":
        scope = replace(scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    elif change == "wrong_agent":
        scope = replace(scope, agent_id="doutor")
    elif change == "wrong_operation":
        scope = replace(scope, operation="medicine.read")
    else:
        scope = replace(scope, property_ids=())
    reader = AsyncMock()
    with pytest.raises(AccessDeniedError):
        await PostgresConsumptionRepository(reader).aggregate_monthly(scope, start=START, end=END)
    reader.fetch_all.assert_not_awaited()


@pytest.mark.parametrize(
    "filters",
    [
        {"start": END, "end": START},
        {"start": START, "end": date.today()},
        {"start": date(2023, 1, 1), "end": END},
        {"medicine_names": ()},
        {"medicine_names": ("'; DROP TABLE application; --",)},
        {"medicine_names": tuple(str(uuid4()) for _ in range(101))},
    ],
)
async def test_invalid_filters_never_query_source(filters):
    reader = AsyncMock()
    with pytest.raises(ValueError):
        await PostgresConsumptionRepository(reader).aggregate_monthly(
            make_scope((uuid4(),)), **{"start": START, "end": END, **filters}
        )
    reader.fetch_all.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        "outside_property",
        "outside_medicine",
        "invalid_quantity",
        "invalid_unit",
        "duplicate_month",
        "incomplete",
    ],
)
async def test_invalid_source_records_fail_closed(change):
    scope, medicine = make_scope((uuid4(),)), uuid4()
    row = aggregate_row(scope, medicine)
    rows = [row]
    if change == "outside_property":
        row["property_id"] = uuid4()
    elif change == "outside_medicine":
        row["medicine_id"] = uuid4()
    elif change == "invalid_quantity":
        row["quantity"] = Decimal("NaN")
    elif change == "invalid_unit":
        row["unit"] = "bottles"
    elif change == "duplicate_month":
        rows.append(dict(row))
    else:
        row["group_count"] = 2
    reader = AsyncMock()
    reader.fetch_all.return_value = rows
    with pytest.raises(InvalidConsumptionDataError):
        await PostgresConsumptionRepository(reader).aggregate_monthly(
            scope, start=START, end=END, medicine_names=("test brand",)
        )


async def test_source_failure_and_cancellation_are_not_empty_history():
    scope = make_scope((uuid4(),))
    reader = AsyncMock()
    repository = PostgresConsumptionRepository(reader)
    reader.fetch_all.side_effect = RuntimeError("private database details")
    with pytest.raises(ConsumptionSourceUnavailableError, match="fonte de consumo indisponível"):
        await repository.aggregate_monthly(scope, start=START, end=END)
    reader.fetch_all.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await repository.aggregate_monthly(scope, start=START, end=END)


async def test_scope_expired_while_query_runs_cannot_return_data(monkeypatch):
    scope, medicine = make_scope((uuid4(),)), uuid4()
    reader = AsyncMock()
    reader.fetch_all.return_value = [aggregate_row(scope, medicine)]
    clock = AsyncMock()
    clock.now = lambda tz: scope.expires_at + timedelta(seconds=1)

    async def expire(*args):
        monkeypatch.setattr(
            "argos_mcp_prediction.infrastructure.consumption_repository.datetime", clock
        )
        return [aggregate_row(scope, medicine)]

    reader.fetch_all.side_effect = expire
    with pytest.raises(AccessDeniedError):
        await PostgresConsumptionRepository(reader).aggregate_monthly(scope, start=START, end=END)
