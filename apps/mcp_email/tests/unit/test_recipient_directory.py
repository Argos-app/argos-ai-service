from uuid import UUID

import pytest
from argos_mcp_email.adapters.recipients import RelationalRecipientDirectory

from packages.contracts import RecipientResolutionStatus, RecipientSelector


class FakeSqlExecutor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch_all(self, query, *parameters):
        self.calls.append((query, parameters))
        return self.rows


@pytest.mark.asyncio
async def test_resolves_active_users_from_app_user_projection() -> None:
    executor = FakeSqlExecutor(
        [
            {
                "id": UUID("00000000-0000-4000-8000-000000000010"),
                "name": "Luis Medeiros",
                "email": "luis.medeiros@gmail.com",
            }
        ]
    )
    directory = RelationalRecipientDirectory(executor)

    result = await directory.resolve(
        (RecipientSelector(kind="email", value="luis.medeiros@gmail.com"),)
    )

    assert result[0].status == RecipientResolutionStatus.RESOLVED
    assert result[0].matches[0].name == "Luis Medeiros"
    assert executor.calls[0][1] == ("luis.medeiros@gmail.com", "email")


@pytest.mark.asyncio
async def test_returns_ambiguous_for_multiple_active_users_with_same_name() -> None:
    executor = FakeSqlExecutor(
        [
            {"id": UUID(int=11), "name": "Matheus Fazan", "email": "one@example.com"},
            {"id": UUID(int=12), "name": "Matheus Fazan", "email": "two@example.com"},
        ]
    )

    result = await RelationalRecipientDirectory(executor).resolve(
        (RecipientSelector(kind="name", value="Matheus Fazan"),)
    )

    assert result[0].status == RecipientResolutionStatus.AMBIGUOUS
