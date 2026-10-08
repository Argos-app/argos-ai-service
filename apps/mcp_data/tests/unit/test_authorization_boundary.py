"""Falhas de autorização devem ocorrer antes de qualquer acesso ao repositório."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_data.application import AccessPolicy, CattleQueryService
from argos_mcp_data.domain import AccessContext
from argos_mcp_data.domain.cattle import ListPropertiesRequest
from argos_mcp_data.domain.errors import AccessDeniedError


class _Directory:
    def __init__(self, property_ids):
        self.property_ids = property_ids

    async def get_active_property_ids(self, user_id):
        return self.property_ids


class _Repository:
    def __init__(self):
        self.calls = 0

    async def list_properties(self, scope, request):
        self.calls += 1
        raise AssertionError("repositório não deveria ser consultado")


def _context(*, agent_id="fazenda", operations=frozenset({"cattle.read"})):
    return AccessContext(
        user_id=uuid4(),
        authorized_property_ids=(uuid4(),),
        operations=operations,
        agent_id=agent_id,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        token_id="test-token",
        request_id="test-request",
    )


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    ("agent_id", "operations", "active_property_ids"),
    [
        ("doutor", frozenset({"medicine.read"}), None),
        ("fazenda", frozenset({"report.read"}), None),
        ("fazenda", frozenset({"cattle.read"}), set()),
    ],
)
async def test_denied_cattle_query_never_calls_repository(
    agent_id, operations, active_property_ids
):
    context = _context(agent_id=agent_id, operations=operations)
    current_properties = active_property_ids or {uuid4()}
    repository = _Repository()
    service = CattleQueryService(AccessPolicy(_Directory(current_properties)), repository)

    with pytest.raises(AccessDeniedError):
        await service.list_properties(context, ListPropertiesRequest())

    assert repository.calls == 0
