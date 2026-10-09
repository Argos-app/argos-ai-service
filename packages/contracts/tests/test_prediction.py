"""Contratos da predição: filtros, estados, isolamento do retorno e Decimal."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts import (
    EstimateMedicineConsumptionRequest,
    EstimateMedicineConsumptionResponse,
    MonthlyConsumption,
    PredictionWindow,
    PropertyCoverage,
)


def test_request_defaults_and_json_uuid_roundtrip():
    property_id, medicine_id = uuid4(), uuid4()
    request = EstimateMedicineConsumptionRequest.model_validate_json(
        json.dumps(
            {
                "property_ids": [str(property_id)],
                "medicine_ids": [str(medicine_id)],
                "as_of": (date.today() - timedelta(days=1)).isoformat(),
            }
        )
    )
    assert request.horizon_days == 28
    assert request.property_ids == (property_id,)
    assert request.medicine_ids == (medicine_id,)
    assert (
        EstimateMedicineConsumptionRequest.model_validate_json(request.model_dump_json()) == request
    )
    with pytest.raises(ValidationError):
        request.horizon_days = 1


@pytest.mark.parametrize("field", ["property_ids", "medicine_ids"])
@pytest.mark.parametrize(
    "value", [[], ["invalid"], [str(uuid4())] * 2, [str(uuid4()) for _ in range(101)]]
)
def test_invalid_uuid_filters_are_rejected(field, value):
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionRequest(as_of="2024-12-31", **{field: value})


@pytest.mark.parametrize("field", ["property_ids", "medicine_ids"])
def test_filter_accepts_up_to_100_distinct_ids(field):
    values = tuple(uuid4() for _ in range(100))
    request = EstimateMedicineConsumptionRequest(as_of="2024-12-31", **{field: values})
    assert getattr(request, field) == values


@pytest.mark.parametrize("value", [0, 29, True, 1.5, "7"])
def test_horizon_requires_integer_in_range(value):
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionRequest(as_of="2024-12-31", horizon_days=value)


@pytest.mark.parametrize(
    "value",
    [
        date.today(),
        date.today() + timedelta(days=1),
        "2024-02-30",
        "2024-01-01T00:00:00",
        "20240101",
        0,
        True,
        datetime(2024, 1, 1),
    ],
)
def test_cutoff_requires_explicit_closed_calendar_date(value):
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionRequest(as_of=value)


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "authorized_property_ids",
        "agent",
        "operations",
        "access_context",
        "coverage",
        "data_origin",
        "mode",
        "sql",
    ],
)
def test_agent_cannot_supply_identity_authority_or_source_metadata(field):
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionRequest(as_of="2024-12-31", **{field: "untrusted"})


@pytest.fixture
def response_payload():
    property_id, medicine_id = str(uuid4()), str(uuid4())
    return {
        "status": "estimated",
        "as_of": "2024-12-31",
        "horizon_days": 28,
        "history_window": {"start": "2024-01-01", "end": "2024-12-31"},
        "forecast_window": {"start": "2025-01-01", "end": "2025-01-28"},
        "property_ids": [property_id],
        "coverage": [
            {
                "property_id": property_id,
                "status": "confirmed",
                "window": {"start": "2024-01-01", "end": "2024-12-31"},
                "source": "test-fixture-annual-coverage",
            }
        ],
        "items": [
            {
                "property_id": property_id,
                "medicine_id": medicine_id,
                "unit": "ml",
                "historical_quantity": "1.230",
                "historical_application_events": 1,
                "historical_head_administrations": 2,
                "monthly_history": [
                    {
                        "month": 1,
                        "calendar_days": 31,
                        "quantity": "1.230",
                        "application_events": 1,
                        "head_administrations": 2,
                    }
                ],
                "estimated_quantity": "0.1234567890123456789",
                "estimated_application_events": "0.5",
                "estimated_head_administrations": "1.0",
            }
        ],
        "mode": "historical",
        "data_origin": "recorded",
        "limitations": ["Consumo histórico não equivale à necessidade sanitária futura."],
    }


def test_response_preserves_decimal_precision_and_schema(response_payload):
    result = EstimateMedicineConsumptionResponse(**response_payload)
    assert result.items[0].estimated_quantity == Decimal("0.1234567890123456789")
    payload = result.model_dump(mode="json")
    assert payload["items"][0]["estimated_quantity"] == "0.1234567890123456789"
    assert (
        EstimateMedicineConsumptionResponse.model_validate_json(result.model_dump_json()) == result
    )
    schema = EstimateMedicineConsumptionRequest.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"property_ids", "medicine_ids", "as_of", "horizon_days"}


@pytest.mark.parametrize("unit", ["ml", "mg", "g", "UI", "dose"])
def test_units_from_current_sql_are_supported_separately(response_payload, unit):
    response_payload["items"][0]["unit"] = unit
    response_payload["items"].append({**response_payload["items"][0], "medicine_id": str(uuid4())})
    assert len(EstimateMedicineConsumptionResponse(**response_payload).items) == 2


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "-Infinity"])
def test_invalid_quantities_are_rejected(response_payload, value):
    response_payload["items"][0]["estimated_quantity"] = value
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionResponse(**response_payload)


@pytest.mark.parametrize(
    "change",
    [
        "outside_property",
        "duplicate_group",
        "duplicate_coverage",
        "missing_coverage",
        "unconfirmed",
        "short_coverage",
        "no_source",
        "missing_estimate",
        "partial_estimate",
        "history_total",
        "duplicate_month",
        "invalid_unit",
        "empty_items",
        "unknown_origin",
        "wrong_history_end",
        "short_history",
        "wrong_forecast",
        "no_limitations",
    ],
)
def test_response_invariants_fail_closed(response_payload, change):
    item = response_payload["items"][0]
    coverage = response_payload["coverage"][0]
    if change == "outside_property":
        item["property_id"] = str(uuid4())
    elif change == "duplicate_group":
        response_payload["items"].append(dict(item))
    elif change == "duplicate_coverage":
        response_payload["coverage"].append(dict(coverage))
    elif change == "missing_coverage":
        response_payload["coverage"] = []
    elif change == "unconfirmed":
        coverage.update(status="unknown", reason="Cobertura não comprovada")
    elif change == "short_coverage":
        coverage["window"]["start"] = "2024-02-01"
    elif change == "no_source":
        coverage["source"] = None
    elif change == "missing_estimate":
        for field in (
            "estimated_quantity",
            "estimated_application_events",
            "estimated_head_administrations",
        ):
            item[field] = None
    elif change == "partial_estimate":
        item["estimated_head_administrations"] = None
    elif change == "history_total":
        item["historical_quantity"] = "2.0"
    elif change == "duplicate_month":
        item["monthly_history"].append(dict(item["monthly_history"][0]))
    elif change == "invalid_unit":
        item["unit"] = "bottles"
    elif change == "empty_items":
        response_payload["items"] = []
    elif change == "unknown_origin":
        response_payload["data_origin"] = "unknown"
    elif change == "wrong_history_end":
        response_payload["history_window"]["end"] = "2024-12-30"
    elif change == "short_history":
        response_payload["history_window"]["start"] = "2024-02-01"
    elif change == "wrong_forecast":
        response_payload["forecast_window"]["start"] = "2024-12-31"
    elif change == "no_limitations":
        response_payload["limitations"] = []
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionResponse(**response_payload)


@pytest.mark.parametrize("status", ["insufficient_data", "inconsistent_data"])
def test_unusable_history_has_null_estimates_with_reason(response_payload, status):
    response_payload.update(status=status, reason="Histórico não utilizável")
    item = response_payload["items"][0]
    for field in (
        "estimated_quantity",
        "estimated_application_events",
        "estimated_head_administrations",
    ):
        item[field] = None
    result = EstimateMedicineConsumptionResponse(**response_payload)
    assert result.items[0].estimated_quantity is None
    response_payload["reason"] = None
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionResponse(**response_payload)


def test_missing_history_is_not_reported_as_zero(response_payload):
    response_payload.update(
        status="insufficient_data",
        reason="Nenhum histórico de aplicações",
        items=[],
        data_origin="unknown",
    )
    response_payload["coverage"] = [
        {
            "property_id": response_payload["property_ids"][0],
            "status": "unknown",
            "reason": "Sem evidência de cobertura",
        }
    ]
    result = EstimateMedicineConsumptionResponse(**response_payload)
    assert result.items == ()


def test_synthetic_origin_requires_simulation(response_payload):
    response_payload["data_origin"] = "synthetic"
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionResponse(**response_payload)
    response_payload["mode"] = "simulation"
    result = EstimateMedicineConsumptionResponse(**response_payload)
    assert result.mode == "simulation"
    response_payload["data_origin"] = "recorded"
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionResponse(**response_payload)


def test_same_medicine_with_different_units_remains_in_separate_groups(response_payload):
    response_payload["items"].append({**response_payload["items"][0], "unit": "mg"})
    result = EstimateMedicineConsumptionResponse(**response_payload)
    assert [item.unit for item in result.items] == ["ml", "mg"]


def test_uuid_duplicates_are_rejected_after_normalization():
    identifier = str(uuid4())
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionRequest(
            as_of="2024-12-31", property_ids=[identifier, identifier.upper()]
        )


@pytest.mark.parametrize("month, days", [(2, 30), (4, 31), (6, 31), (9, 31), (11, 31)])
def test_impossible_calendar_days_are_rejected(month, days):
    with pytest.raises(ValidationError):
        MonthlyConsumption(
            month=month,
            calendar_days=days,
            quantity="0",
            application_events=0,
            head_administrations=0,
        )


def test_inverted_window_and_coverage_without_reason_are_rejected():
    with pytest.raises(ValidationError):
        PredictionWindow(start="2024-12-31", end="2024-01-01")
    with pytest.raises(ValidationError):
        PropertyCoverage(property_id=uuid4(), status="unknown")


@pytest.mark.parametrize("value", [0, 13, True, "1"])
def test_month_must_be_a_calendar_month(value):
    with pytest.raises(ValidationError):
        MonthlyConsumption(
            month=value,
            calendar_days=31,
            quantity="0",
            application_events=0,
            head_administrations=0,
        )


def test_excessive_results_are_rejected(response_payload):
    response_payload["items"] = [
        {**response_payload["items"][0], "medicine_id": str(uuid4())} for _ in range(1001)
    ]
    with pytest.raises(ValidationError):
        EstimateMedicineConsumptionResponse(**response_payload)
