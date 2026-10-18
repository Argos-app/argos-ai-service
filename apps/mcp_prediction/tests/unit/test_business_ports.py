"""Portas exercitadas com dependências falsas, sem SQL, SDK ou fonte presumida."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from argos_mcp_prediction.domain import (
    ConsumptionGroup,
    ConsumptionUnit,
    CoverageRecord,
    CoverageState,
    MonthlyConsumptionRecord,
)
from argos_mcp_prediction.domain.errors import ConsumptionSourceUnavailableError
from argos_mcp_prediction.ports import ConsumptionReadRepository, CoverageReadRepository

from packages.security.access import AccessContext, AuthorizedScope

pytestmark = pytest.mark.asyncio


class FakeConsumptionRepository:
    def __init__(self, records: tuple[tuple[date, MonthlyConsumptionRecord], ...]):
        self.records = records

    async def aggregate_monthly(
        self,
        scope: AuthorizedScope,
        *,
        start: date,
        end: date,
        medicine_names: tuple[str, ...] | None = None,
    ) -> tuple[MonthlyConsumptionRecord, ...]:
        return tuple(
            record
            for observed_on, record in self.records
            if start <= observed_on <= end
            and record.group.property_id in scope.property_ids
            and (
                medicine_names is None
                or any(
                    name.casefold() in record.group.medicine_name.casefold()
                    or name.casefold() in record.group.active_ingredient.casefold()
                    for name in medicine_names
                )
            )
        )


class FakeCoverageRepository:
    def __init__(self, evidence: tuple[CoverageRecord, ...]):
        self.evidence = {record.property_id: record for record in evidence}

    async def get_coverage(
        self,
        scope: AuthorizedScope,
        *,
        start: date,
        end: date,
    ) -> tuple[CoverageRecord, ...]:
        return tuple(
            self.evidence.get(
                identifier,
                CoverageRecord(
                    identifier,
                    CoverageState.UNKNOWN,
                    reason="Sem evidência confiável de acompanhamento",
                ),
            )
            for identifier in scope.property_ids
        )


def make_scope(properties):
    now = datetime.now(UTC)
    context = AccessContext(
        user_id=uuid4(),
        authorized_property_ids=properties,
        operations=frozenset({"prediction.read"}),
        agent_id="analyst",
        issued_at=now,
        expires_at=now + timedelta(seconds=120),
        token_id="token",
        request_id="request",
    )
    return AuthorizedScope.build(
        context, current_property_ids=set(properties), operation="prediction.read"
    )


def record(property_id, medicine_id, unit=ConsumptionUnit.ML):
    return MonthlyConsumptionRecord(
        ConsumptionGroup(
            property_id, medicine_id, unit, f"Medicine {medicine_id}", f"Ingredient {medicine_id}"
        ),
        1,
        Decimal("1.230"),
        1,
        2,
    )


async def test_consumption_dependency_preserves_scope_date_boundaries_and_medicine_filter():
    a, b, medicine, other_medicine = uuid4(), uuid4(), uuid4(), uuid4()
    first, last = record(a, medicine), record(a, medicine, ConsumptionUnit.MG)
    repository: ConsumptionReadRepository = FakeConsumptionRepository(
        (
            (date(2024, 1, 1), first),
            (date(2024, 1, 31), last),
            (date(2024, 1, 10), record(b, medicine)),
            (date(2024, 1, 10), record(a, other_medicine)),
            (date(2023, 12, 31), record(a, medicine)),
            (date(2024, 2, 1), record(a, medicine)),
        )
    )
    scope = make_scope((a,))
    selected = await repository.aggregate_monthly(
        scope,
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        medicine_names=(f"Medicine {medicine}",),
    )
    assert selected == (first, last)
    assert selected[0].quantity == Decimal("1.230")
    unfiltered = await repository.aggregate_monthly(
        scope, start=date(2024, 1, 1), end=date(2024, 1, 31)
    )
    assert len(unfiltered) == 3
    absent = await repository.aggregate_monthly(
        scope, start=date(2024, 1, 1), end=date(2024, 1, 31), medicine_names=("missing",)
    )
    assert absent == ()


async def test_coverage_dependency_uses_trusted_evidence_only_for_scope():
    a, b, outside = uuid4(), uuid4(), uuid4()
    start, end = date(2024, 1, 1), date(2024, 12, 31)
    evidence = CoverageRecord(a, CoverageState.CONFIRMED, start, end, "trusted-fixture")
    repository: CoverageReadRepository = FakeCoverageRepository(
        (
            evidence,
            CoverageRecord(outside, CoverageState.CONFIRMED, start, end, "outside-fixture"),
        )
    )
    result = await repository.get_coverage(make_scope((a, b)), start=start, end=end)
    assert result[0] == evidence
    assert result[1].property_id == b
    assert result[1].state == CoverageState.UNKNOWN
    assert result[1].source is None
    assert result[1].start is None and result[1].end is None
    assert result[1].reason
    assert {item.property_id for item in result} == {a, b}


async def test_dependency_failure_is_distinct_from_no_history():
    class UnavailableRepository:
        async def aggregate_monthly(self, scope, *, start, end, medicine_names=None):
            raise ConsumptionSourceUnavailableError("Fonte indisponível")

    repository: ConsumptionReadRepository = UnavailableRepository()
    with pytest.raises(ConsumptionSourceUnavailableError):
        await repository.aggregate_monthly(
            make_scope((uuid4(),)), start=date(2024, 1, 1), end=date(2024, 12, 31)
        )


async def test_property_reads_require_scope_and_do_not_accept_agent_evidence():
    consumption: ConsumptionReadRepository = FakeConsumptionRepository(())
    coverage: CoverageReadRepository = FakeCoverageRepository(())
    dates = {"start": date(2024, 1, 1), "end": date(2024, 12, 31)}
    with pytest.raises(TypeError):
        await consumption.aggregate_monthly(**dates)
    with pytest.raises(TypeError):
        await coverage.get_coverage(**dates)
    with pytest.raises(TypeError):
        await coverage.get_coverage(make_scope((uuid4(),)), **dates, evidence="agent-claim")
