"""Escopo reduzido, revogação e revalidação antes de consultas de negócio."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from packages.security.access import AccessContext
from packages.security.access_directory import UserPropertyAccess
from packages.security.access_policy import AccessPolicy
from packages.security.errors import AccessDeniedError

pytestmark = pytest.mark.asyncio


def make_context(properties):
    return AccessContext(
        firebase_uid="firebase-user-123",
        authorized_property_ids=properties,
        operations=frozenset({"prediction.read"}),
        agent_id="analista",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
        token_id="token",
        request_id="request",
    )


async def test_selection_restricts_context_and_never_uses_extra_current_properties():
    a, b, c = uuid4(), uuid4(), uuid4()
    directory = AsyncMock()
    user_id = uuid4()
    directory.get_user_property_access.return_value = UserPropertyAccess(user_id, {a, b, c})
    policy = AccessPolicy(directory)
    context = make_context((a, b))
    scope = await policy.authorize(context, operation="prediction.read")
    assert scope.property_ids == (a, b)
    scope = await policy.authorize(context, operation="prediction.read", selected_property_ids=(b,))
    assert scope.property_ids == (b,)
    directory.get_user_property_access.assert_awaited_with(context.firebase_uid)
    for selection in ((c,), (a, c), (), (a, a)):
        with pytest.raises(AccessDeniedError):
            await policy.authorize(
                context, operation="prediction.read", selected_property_ids=selection
            )


async def test_revocation_rejects_whole_delegation_even_with_authorized_selection():
    a, b = uuid4(), uuid4()
    directory = AsyncMock()
    user_id = uuid4()
    directory.get_user_property_access.side_effect = [
        UserPropertyAccess(user_id, {a, b}),
        UserPropertyAccess(user_id, {a}),
    ]
    policy = AccessPolicy(directory)
    context = make_context((a, b))
    await policy.authorize(context, operation="prediction.read")
    with pytest.raises(AccessDeniedError):
        await policy.authorize(context, operation="prediction.read", selected_property_ids=(a,))
    assert directory.get_user_property_access.await_count == 2


async def test_missing_or_expired_context_does_not_query_directory():
    directory = AsyncMock()
    policy = AccessPolicy(directory)
    expired = replace(make_context((uuid4(),)), expires_at=datetime.now(UTC) - timedelta(seconds=1))
    for context in (None, expired):
        with pytest.raises(AccessDeniedError):
            await policy.authorize(context, operation="prediction.read")
    directory.get_user_property_access.assert_not_awaited()


async def test_no_current_access_fails_closed():
    directory = AsyncMock()
    directory.get_user_property_access.return_value = None
    with pytest.raises(AccessDeniedError):
        await AccessPolicy(directory).authorize(
            make_context((uuid4(),)), operation="prediction.read"
        )


async def test_expiration_during_directory_query_is_rejected(monkeypatch):
    a = uuid4()
    context = make_context((a,))
    directory = AsyncMock()

    directory.get_user_property_access.return_value = UserPropertyAccess(uuid4(), {a})
    clock = MagicMock()
    clock.now.side_effect = [context.issued_at, context.expires_at + timedelta(seconds=1)]
    monkeypatch.setattr("packages.security.access_policy.datetime", clock)
    with pytest.raises(AccessDeniedError):
        await AccessPolicy(directory).authorize(context, operation="prediction.read")


async def test_operation_and_property_limit_are_enforced():
    properties = tuple(UUID(int=i) for i in range(1, 102))
    directory = AsyncMock()
    directory.get_user_property_access.return_value = UserPropertyAccess(uuid4(), set(properties))
    policy = AccessPolicy(directory)
    with pytest.raises(AccessDeniedError):
        await policy.authorize(make_context(properties), operation="prediction.read")
    scope = await policy.authorize(
        make_context(properties),
        operation="prediction.read",
        selected_property_ids=properties[:100],
    )
    assert len(scope.property_ids) == 100
    with pytest.raises(AccessDeniedError):
        await policy.authorize(make_context(properties[:1]), operation="medicine.read")
