"""Cobertura anual e cálculo sazonal, sem infraestrutura ou consultas."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, Inexact, localcontext
from uuid import uuid4

import pytest
from argos_mcp_prediction.domain import (
    ConsumptionGroup,
    ConsumptionState,
    ConsumptionUnit,
    CoverageRecord,
    CoverageState,
    MonthlyConsumptionRecord,
)
from argos_mcp_prediction.domain.errors import InvalidConsumptionDataError
from argos_mcp_prediction.domain.rules import (
    build_prediction_windows,
    calculate_consumption,
    coverage_failure_reason,
)

TODAY = date(2026, 1, 1)


def group(unit=ConsumptionUnit.ML):
    return ConsumptionGroup(uuid4(), uuid4(), unit, "Marca", "Ingrediente")


def confirmed(selected, windows):
    return CoverageRecord(
        selected.property_id,
        CoverageState.CONFIRMED,
        windows.history_start,
        windows.history_end,
        "trusted-annual-fixture",
    )


def record(selected, month, quantity, events=1, heads=1):
    return MonthlyConsumptionRecord(selected, month, Decimal(quantity), events, heads)


@pytest.mark.parametrize(
    "cutoff, start, days",
    [
        (date(2023, 12, 31), date(2023, 1, 1), 365),
        (date(2024, 12, 31), date(2024, 1, 1), 366),
        (date(2024, 2, 29), date(2023, 3, 1), 366),
        (date(2025, 2, 28), date(2024, 2, 29), 366),
        (date(2024, 3, 15), date(2023, 3, 16), 366),
    ],
)
def test_annual_windows_are_inclusive_and_handle_leap_anniversary(cutoff, start, days):
    windows = build_prediction_windows(cutoff, today=TODAY)
    assert windows.history_start == start
    assert windows.history_end == cutoff
    assert (cutoff - start).days + 1 == days
    assert windows.forecast_start == cutoff + timedelta(days=1)
    assert windows.forecast_end == cutoff + timedelta(days=28)


@pytest.mark.parametrize(
    "values",
    [
        {"as_of": TODAY},
        {"as_of": TODAY + timedelta(days=1)},
        {"as_of": date(1, 1, 1)},
        {"as_of": "2024-12-31"},
        {"horizon_days": 0},
        {"horizon_days": 29},
        {"horizon_days": True},
        {"horizon_days": "7"},
    ],
)
def test_invalid_cutoff_or_horizon_is_rejected(values):
    with pytest.raises(ValueError):
        build_prediction_windows(**{"as_of": date(2024, 12, 31), "today": TODAY, **values})


def test_daily_means_cover_forecast_crossing_months_and_split_history_month():
    selected = group()
    windows = build_prediction_windows(date(2024, 3, 15), today=TODAY)
    records = (record(selected, 3, "62", 31, 62), record(selected, 4, "90", 30, 60))
    result = calculate_consumption(
        selected, records, coverage=confirmed(selected, windows), windows=windows
    )
    assert result.estimate.state == ConsumptionState.ESTIMATED
    assert result.estimate.quantity == Decimal(68)  # March: 16 * 2; April: 12 * 3.
    assert result.estimate.application_events == 28
    assert result.estimate.head_administrations == 56
    assert result.historical_quantity == 152
    assert result.historical_application_events == 61
    assert result.historical_head_administrations == 122
    assert len(result.monthly_history) == 12
    assert result.monthly_history[2].calendar_days == 31
    assert sum(item.calendar_days for item in result.monthly_history) == 366


def test_forecast_uses_history_leap_february_denominator_not_future_month_length():
    selected = group()
    windows = build_prediction_windows(date(2025, 1, 31), today=TODAY)
    result = calculate_consumption(
        selected,
        (record(selected, 2, "29", 29, 58),),
        coverage=confirmed(selected, windows),
        windows=windows,
    )
    assert windows.forecast_end == date(2025, 2, 28)
    assert result.monthly_history[1].calendar_days == 29
    assert result.estimate.quantity == 28
    assert result.estimate.application_events == 28
    assert result.estimate.head_administrations == 56


def test_full_covered_month_preserves_decimal_quantity_and_event_totals():
    selected = group()
    windows = build_prediction_windows(date(2023, 1, 31), today=TODAY)
    result = calculate_consumption(
        selected,
        (record(selected, 2, "0.1", 1, 2),),
        coverage=confirmed(selected, windows),
        windows=windows,
    )
    assert result.estimate.quantity == Decimal("0.1")
    assert result.estimate.application_events == 1
    assert result.estimate.head_administrations == 2


def test_forecast_can_include_february_29_and_year_boundary():
    selected = group()
    leap = build_prediction_windows(date(2024, 2, 1), today=TODAY)
    result = calculate_consumption(
        selected,
        (record(selected, 2, "28", 28, 28),),
        coverage=confirmed(selected, leap),
        windows=leap,
    )
    assert leap.forecast_end == date(2024, 2, 29)
    assert result.monthly_history[1].calendar_days == 28
    assert result.estimate.quantity == 28
    year = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    result = calculate_consumption(
        selected,
        (record(selected, 1, "31", 31, 31),),
        coverage=confirmed(selected, year),
        windows=year,
    )
    assert year.forecast_start == date(2025, 1, 1)
    assert result.estimate.quantity == 28


@pytest.mark.parametrize("state", [CoverageState.UNKNOWN, CoverageState.INCOMPLETE])
def test_unconfirmed_coverage_does_not_block_estimate_and_fills_missing_months(state):
    selected = group()
    windows = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    evidence = CoverageRecord(selected.property_id, state, reason="Cobertura não comprovada")
    result = calculate_consumption(
        selected,
        (record(selected, 1, "31", 31, 31),),
        coverage=evidence,
        windows=windows,
    )
    assert result.estimate.state == ConsumptionState.ESTIMATED
    assert result.estimate.quantity == 28
    assert result.estimate.application_events == 28
    assert result.estimate.head_administrations == 28
    assert len(result.monthly_history) == 12


@pytest.mark.parametrize(
    "change", ["missing", "no_source", "no_window", "short_start", "short_end"]
)
def test_unusable_coverage_does_not_block_estimation(change):
    selected = group()
    windows = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    evidence = confirmed(selected, windows)
    if change == "missing":
        evidence = None
    elif change == "no_source":
        evidence = replace(evidence, source=None)
    elif change == "no_window":
        evidence = replace(evidence, start=None)
    elif change == "short_start":
        evidence = replace(evidence, start=windows.history_start + timedelta(days=1))
    else:
        evidence = replace(evidence, end=windows.history_end - timedelta(days=1))
    result = calculate_consumption(
        selected, (record(selected, 1, "31"),), coverage=evidence, windows=windows
    )
    assert result.estimate.state == ConsumptionState.ESTIMATED
    assert result.estimate.quantity == 28
    assert len(result.monthly_history) == 12


def test_no_product_history_is_not_zero_even_with_confirmed_coverage():
    selected = group()
    windows = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    result = calculate_consumption(
        selected, (), coverage=confirmed(selected, windows), windows=windows
    )
    assert result.estimate.quantity is None
    assert result.estimate.state == ConsumptionState.INSUFFICIENT_DATA
    assert result.monthly_history == ()
    assert "sem histórico" in result.estimate.reason


def test_zero_for_a_missing_month_does_not_require_coverage_confirmation():
    selected = group()
    windows = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    records = (record(selected, 2, "29"),)
    result = calculate_consumption(
        selected, records, coverage=confirmed(selected, windows), windows=windows
    )
    assert result.estimate.state == ConsumptionState.ESTIMATED
    assert result.estimate.quantity == 0  # Missing January is assumed to have no consumption.
    assert len(result.monthly_history) == 12
    unknown = calculate_consumption(selected, records, coverage=None, windows=windows)
    assert unknown.estimate.quantity == 0
    assert len(unknown.monthly_history) == 12


def test_decimal_calculation_is_independent_of_ambient_precision_and_traps():
    selected = group()
    windows = build_prediction_windows(date(2024, 12, 31), horizon_days=1, today=TODAY)
    records = (record(selected, 1, "0.1234567890123456789"),)
    evidence = confirmed(selected, windows)
    expected = calculate_consumption(selected, records, coverage=evidence, windows=windows)
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        actual = calculate_consumption(selected, records, coverage=evidence, windows=windows)
        assert context.prec == 3 and context.traps[Inexact]
    assert actual == expected
    assert actual.historical_quantity == Decimal("0.1234567890123456789")
    assert isinstance(actual.estimate.quantity, Decimal)
    assert actual.estimate.application_events > 0
    assert actual.estimate.application_events < 1  # Fractional counts remain estimates.


@pytest.mark.parametrize(
    "change",
    [
        "outside_group",
        "duplicate_month",
        "negative",
        "nan",
        "float",
        "invalid_month",
        "bool_events",
        "zero_events",
        "few_heads",
    ],
)
def test_inconsistent_history_is_an_error_not_zero(change):
    selected = group()
    windows = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    value = record(selected, 1, "31")
    records = (value,)
    changes = {
        "outside_group": {"group": group()},
        "negative": {"quantity": Decimal(-1)},
        "nan": {"quantity": Decimal("NaN")},
        "float": {"quantity": 1.5},
        "invalid_month": {"month": 13},
        "bool_events": {"application_events": True},
        "zero_events": {"application_events": 0},
        "few_heads": {"head_administrations": 0},
    }
    if change == "duplicate_month":
        records = (value, value)
    else:
        records = (replace(value, **changes[change]),)
    with pytest.raises(InvalidConsumptionDataError):
        calculate_consumption(
            selected, records, coverage=confirmed(selected, windows), windows=windows
        )


def test_units_and_properties_remain_separate():
    selected = group()
    mg = replace(selected, unit=ConsumptionUnit.MG)
    windows = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    ml_result = calculate_consumption(
        selected,
        (record(selected, 1, "31"),),
        coverage=confirmed(selected, windows),
        windows=windows,
    )
    mg_result = calculate_consumption(
        mg, (record(mg, 1, "62"),), coverage=confirmed(mg, windows), windows=windows
    )
    assert ml_result.estimate.group.unit == ConsumptionUnit.ML
    assert mg_result.estimate.group.unit == ConsumptionUnit.MG
    assert ml_result.estimate.quantity == 28 and mg_result.estimate.quantity == 56
    with pytest.raises(InvalidConsumptionDataError):
        coverage_failure_reason(
            confirmed(group(), windows), property_id=selected.property_id, windows=windows
        )


def test_short_history_window_and_invalid_evidence_are_rejected():
    selected = group()
    windows = build_prediction_windows(date(2024, 12, 31), today=TODAY)
    with pytest.raises(ValueError):
        calculate_consumption(
            selected, (), coverage=None, windows=replace(windows, history_start=date(2024, 12, 1))
        )
    evidence = replace(confirmed(selected, windows), start=date(2025, 1, 1))
    with pytest.raises(InvalidConsumptionDataError):
        coverage_failure_reason(evidence, property_id=selected.property_id, windows=windows)
