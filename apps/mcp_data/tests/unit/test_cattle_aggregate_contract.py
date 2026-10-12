"""Valida os filtros públicos dos agregados antes de consultar o banco."""

from uuid import uuid4

import pytest
from argos_mcp_data.domain.cattle_aggregates import ExceptionSummaryRequest, FarmSummaryRequest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "payload",
    [
        {"head_count_min": -1},
        {"head_count_max": -1},
        {"head_count_min": 20, "head_count_max": 1},
        {"group_by": "l.id; DELETE FROM lot"},
    ],
)
def test_farm_summary_rejects_invalid_filters(payload):
    with pytest.raises(ValidationError):
        FarmSummaryRequest.model_validate(payload)


@pytest.mark.parametrize("request_type", [FarmSummaryRequest, ExceptionSummaryRequest])
def test_aggregate_requests_reject_duplicate_properties(request_type):
    property_id = uuid4()
    with pytest.raises(ValidationError, match="duplicatas"):
        request_type(property_ids=(property_id, property_id))


@pytest.mark.parametrize("ear_tag", ["", "A" * 21])
def test_exception_summary_rejects_invalid_ear_tags(ear_tag):
    with pytest.raises(ValidationError):
        ExceptionSummaryRequest(ear_tag=ear_tag)
