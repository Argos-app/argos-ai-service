from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_prediction.domain import CoverageState
from argos_mcp_prediction.infrastructure.postgres_coverage_repository import (
    PostgresCoverageRepository,
)

from packages.security.access import AccessContext, AuthorizedScope


def make_scope(properties):
    now = datetime.now(UTC)
    context = AccessContext(
        user_id=uuid4(),
        authorized_property_ids=properties,
        operations=frozenset({"prediction.read"}),
        agent_id="analista",
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
        token_id="test",
        request_id="test",
    )
    return AuthorizedScope.build(
        context, current_property_ids=set(properties), operation="prediction.read"
    )


class FakeSqlReader:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.parameters = None

    async def fetch_all(self, query, *parameters):
        self.query = query
        self.parameters = parameters
        return self.rows


@pytest.mark.asyncio
async def test_contiguous_validated_intervals_confirm_full_requested_window():
    property_id = uuid4()
    start = date(2025, 1, 1)
    middle = date(2025, 7, 1)
    end = date(2025, 12, 31)
    reader = FakeSqlReader(
        [
            {
                "property_id": property_id,
                "coverage_start": start,
                "coverage_end": middle - timedelta(days=1),
                "evidence_source": "validated_import_1",
            },
            {
                "property_id": property_id,
                "coverage_start": middle,
                "coverage_end": end,
                "evidence_source": "validated_import_2",
            },
        ]
    )

    result = await PostgresCoverageRepository(reader, "ops.property_coverage").get_coverage(
        make_scope((property_id,)), start=start, end=end
    )

    assert result[0].state == CoverageState.CONFIRMED
    assert result[0].start == start
    assert result[0].end == end
    assert "validated_import_1" in result[0].source
    assert 'FROM "ops"."property_coverage"' in reader.query
    assert reader.parameters == ([property_id], start, end)


@pytest.mark.asyncio
async def test_gap_and_absent_evidence_fail_closed():
    property_id = uuid4()
    start = date(2025, 1, 1)
    end = date(2025, 12, 31)
    reader = FakeSqlReader(
        [
            {
                "property_id": property_id,
                "coverage_start": start,
                "coverage_end": date(2025, 6, 30),
                "evidence_source": "partial_import",
            }
        ]
    )
    repository = PostgresCoverageRepository(reader, "property_coverage")

    result = await repository.get_coverage(make_scope((property_id, uuid4())), start=start, end=end)

    assert result[0].state == CoverageState.INCOMPLETE
    assert result[1].state == CoverageState.UNKNOWN


@pytest.mark.parametrize("relation", ["public.foo; DROP TABLE property", "foo bar", "a.b.c"])
def test_relation_identifier_is_restricted(relation):
    with pytest.raises(ValueError, match="relação"):
        PostgresCoverageRepository(FakeSqlReader([]), relation)
