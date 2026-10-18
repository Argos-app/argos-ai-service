"""Fluxo autorizado: resolução por nome, cálculo e dados insuficientes/inválidos."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from argos_mcp_prediction.application import EstimateMedicineConsumption
from argos_mcp_prediction.domain import (
    ConsumptionGroup,
    ConsumptionUnit,
    MonthlyConsumptionRecord,
)
from argos_mcp_prediction.domain.errors import (
    ConsumptionSourceUnavailableError,
    InvalidConsumptionDataError,
)
from argos_mcp_prediction.infrastructure import UnknownCoverageRepository

from packages.contracts import DataOrigin, EstimateMedicineConsumptionRequest
from packages.security.access import AccessContext
from packages.security.access_policy import AccessPolicy
from packages.security.errors import AccessDeniedError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def scenario():
    property_id = uuid4()
    now = datetime.now(UTC)
    context = AccessContext(
        uuid4(),
        (property_id,),
        frozenset({"prediction.read"}),
        "analista",
        now,
        now + timedelta(minutes=2),
        "token",
        "request",
    )
    directory = AsyncMock()
    directory.get_active_property_ids.return_value = {property_id}
    repository = AsyncMock()
    group = ConsumptionGroup(
        property_id, uuid4(), ConsumptionUnit.ML, "Marca de teste", "Ivermectina"
    )
    repository.aggregate_monthly.return_value = (
        MonthlyConsumptionRecord(group, 1, Decimal("310"), 31, 62),
    )

    def service(origin=DataOrigin.RECORDED):
        return EstimateMedicineConsumption(
            access_policy=AccessPolicy(directory),
            consumption_repository=repository,
            coverage_repository=UnknownCoverageRepository(),
            data_origin=origin,
        )

    return context, directory, repository, group, service


def request(names=("ivermectina",), **filters):
    return EstimateMedicineConsumptionRequest(
        as_of="2024-12-31", horizon_days=1, medicine_names=names, **filters
    )


async def test_resolved_name_produces_labeled_estimate_without_coverage_confirmation(scenario):
    context, directory, repository, group, service = scenario
    response = await service().execute(context, request())
    assert response.status == "estimated"
    assert response.coverage[0].status == "unknown"
    assert response.items[0].estimated_quantity == 10
    assert response.items[0].medicine_id == group.medicine_id
    assert response.items[0].medicine_name == "Marca de teste"
    assert response.items[0].active_ingredient == "Ivermectina"
    assert response.items[0].historical_quantity == 310
    scope = repository.aggregate_monthly.await_args.args[0]
    assert scope.property_ids == context.authorized_property_ids
    assert repository.aggregate_monthly.await_args.kwargs["medicine_names"] == ("ivermectina",)
    directory.get_active_property_ids.assert_awaited_once_with(context.user_id)


@pytest.mark.parametrize("names", [("ivermectina", "missing"), ("missing",)])
async def test_unresolved_names_never_produce_partial_or_zero_estimates(scenario, names):
    context, _, repository, _, service = scenario
    if names == ("missing",):
        repository.aggregate_monthly.return_value = ()
    response = await service().execute(context, request(names))
    assert response.status == "insufficient_data"
    assert response.reason
    assert all(item.estimated_quantity is None for item in response.items)
    if names == ("missing",):
        assert response.items == ()


async def test_multiple_medicines_matching_one_ingredient_remain_separate(scenario):
    context, _, repository, group, service = scenario
    other = replace(
        group, medicine_id=uuid4(), medicine_name="Outra marca", unit=ConsumptionUnit.MG
    )
    repository.aggregate_monthly.return_value += (
        MonthlyConsumptionRecord(other, 1, Decimal("62"), 2, 4),
    )
    response = await service().execute(context, request())
    assert response.status == "estimated"
    assert len(response.items) == 2
    assert {item.unit for item in response.items} == {"ml", "mg"}
    assert {item.estimated_quantity for item in response.items} == {Decimal(10), Decimal(2)}


async def test_name_without_history_in_one_selected_farm_blocks_partial_estimates(scenario):
    context, directory, _, _, service = scenario
    other_property = uuid4()
    context = replace(
        context, authorized_property_ids=(*context.authorized_property_ids, other_property)
    )
    directory.get_active_property_ids.return_value.add(other_property)
    response = await service().execute(context, request())
    assert response.status == "insufficient_data"
    assert response.reason
    assert response.items[0].estimated_quantity is None


@pytest.mark.parametrize("change", ["absent_context", "wrong_agent", "revoked", "outside_filter"])
async def test_denied_access_never_queries_history(scenario, change):
    context, directory, repository, _, service = scenario
    payload = request()
    if change == "absent_context":
        context = None
    elif change == "wrong_agent":
        context = replace(context, agent_id="doutor")
    elif change == "revoked":
        directory.get_active_property_ids.return_value = set()
    else:
        payload = request(property_ids=(uuid4(),))
    with pytest.raises(AccessDeniedError):
        await service().execute(context, payload)
    repository.aggregate_monthly.assert_not_awaited()


@pytest.mark.parametrize("change", ["outside_property", "ignored_name", "duplicate_month"])
async def test_invalid_adapter_results_cannot_be_used_for_estimates(scenario, change):
    context, _, repository, group, service = scenario
    if change == "duplicate_month":
        repository.aggregate_monthly.return_value *= 2
        response = await service().execute(context, request())
        assert response.status == "inconsistent_data"
        assert response.items == ()
        return
    group = (
        replace(group, property_id=uuid4())
        if change == "outside_property"
        else replace(group, medicine_name="Other", active_ingredient="Other")
    )
    repository.aggregate_monthly.return_value = (
        MonthlyConsumptionRecord(group, 1, Decimal(1), 1, 1),
    )
    with pytest.raises(InvalidConsumptionDataError):
        await service().execute(context, request())


async def test_database_inconsistency_and_source_failure_are_distinct(scenario):
    context, _, repository, _, service = scenario
    repository.aggregate_monthly.side_effect = InvalidConsumptionDataError("invalid applications")
    response = await service().execute(context, request())
    assert response.status == "inconsistent_data"
    assert response.items == ()
    repository.aggregate_monthly.side_effect = ConsumptionSourceUnavailableError("unavailable")
    with pytest.raises(ConsumptionSourceUnavailableError):
        await service().execute(context, request())


async def test_unknown_data_origin_blocks_numbers_and_synthetic_origin_is_simulation(scenario):
    context, _, _, _, service = scenario
    unknown = await service(DataOrigin.UNKNOWN).execute(context, request())
    assert unknown.status == "insufficient_data"
    assert unknown.items[0].estimated_quantity is None
    synthetic = await service(DataOrigin.SYNTHETIC).execute(context, request())
    assert synthetic.status == "estimated"
    assert synthetic.mode == "simulation"
