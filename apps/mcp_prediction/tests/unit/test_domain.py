"""Modelos internos seguem as dataclasses imutáveis do MCP de e-mail."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
from uuid import uuid4

import pytest
from argos_mcp_prediction.domain import (
    ConsumptionEstimate,
    ConsumptionGroup,
    ConsumptionState,
    ConsumptionUnit,
    CoverageRecord,
    CoverageState,
    MonthlyConsumptionRecord,
)
from argos_mcp_prediction.domain.errors import (
    ConsumptionResultLimitError,
    ConsumptionSourceUnavailableError,
    DomainError,
    InvalidConsumptionDataError,
)


def test_groups_preserve_property_medicine_and_unit():
    property_id, medicine_id = uuid4(), uuid4()
    ml = ConsumptionGroup(property_id, medicine_id, ConsumptionUnit.ML)
    mg = ConsumptionGroup(property_id, medicine_id, ConsumptionUnit.MG)
    other = ConsumptionGroup(uuid4(), medicine_id, ConsumptionUnit.ML)
    assert len({ml, mg, other}) == 3
    with pytest.raises(FrozenInstanceError):
        ml.unit = ConsumptionUnit.MG
    record = MonthlyConsumptionRecord(ml, 1, Decimal("1.230"), 1, 2)
    assert record.quantity == Decimal("1.230")
    assert record.head_administrations == 2


def test_unknown_coverage_and_insufficient_estimate_do_not_default_to_zero():
    group = ConsumptionGroup(uuid4(), uuid4(), ConsumptionUnit.ML)
    coverage = CoverageRecord(group.property_id, CoverageState.UNKNOWN)
    estimate = ConsumptionEstimate(group, ConsumptionState.INSUFFICIENT_DATA)
    assert coverage.source is None
    assert estimate.quantity is None
    assert estimate.application_events is None
    assert estimate.head_administrations is None


@pytest.mark.parametrize(
    "error, code",
    [
        (InvalidConsumptionDataError, "inconsistent_data"),
        (ConsumptionSourceUnavailableError, "consumption_source_unavailable"),
        (ConsumptionResultLimitError, "consumption_result_limit_exceeded"),
    ],
)
def test_domain_errors_have_stable_codes(error, code):
    assert issubclass(error, DomainError)
    assert error.code == code
