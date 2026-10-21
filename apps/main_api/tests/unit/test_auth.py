import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.dependencies.auth import (
    _principal_from_access_token,
    _verify_access_token,
    to_actor_context,
)
from app.domain.user_access import UserAccess, UserFarm
from app.infrastructure.access_tokens import MainApiAccessTokenCodec
from app.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException


def _codec() -> MainApiAccessTokenCodec:
    key = Ed25519PrivateKey.generate()
    encoded_key = base64.b64encode(
        key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode()
    return MainApiAccessTokenCodec(
        Settings(
            access_token_key_id="main-api-key",
            access_token_private_key=encoded_key,
        )
    )


def _request(codec: MainApiAccessTokenCodec) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(access_token_codec=codec)))


class _UserRepository:
    def __init__(self, user: UserAccess | None) -> None:
        self.user = user

    async def find_by_firebase_uid(self, firebase_uid):
        if self.user is None or firebase_uid != self.user.firebase_uid:
            return None
        return self.user


def _user(firebase_uid: str = "firebase-user-123") -> UserAccess:
    return UserAccess(
        user_id=uuid4(),
        firebase_uid=firebase_uid,
        name="Usuário",
        email="user@example.com",
        active=True,
        blocked_until=None,
        permission_code="USER",
        permission_name="Usuário",
        permission_active=True,
        farms=(UserFarm(farm_id=uuid4(), name="Fazenda"),),
    )


def _principal_request(codec, token, user):
    state = SimpleNamespace(
        access_token_codec=codec,
        user_access_repository=_UserRepository(user),
        settings=Settings(),
        user_usage_counter=None,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers={"Authorization": f"Bearer {token}"},
    )


def test_api_accepts_jwt_issued_by_its_configured_signer() -> None:
    codec = _codec()
    user_id = uuid4()

    claims = _verify_access_token(_request(codec), codec.issue(user_id, "firebase-user-123"))

    assert claims.user_id == user_id


def test_api_rejects_token_from_another_signer_without_legacy_fallback() -> None:
    issuer = _codec()
    verifier = _codec()
    token = issuer.issue(uuid4(), "firebase-other-user")

    with pytest.raises(HTTPException) as error:
        _verify_access_token(_request(verifier), token)

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_principal_revalidates_firebase_uid_and_builds_actor_context() -> None:
    codec = _codec()
    user = _user()
    token = codec.issue(user.user_id, user.firebase_uid)

    principal = await _principal_from_access_token(_principal_request(codec, token, user), token)
    actor = to_actor_context(principal)

    assert principal.operations == frozenset(
        {
            "ai.sessions.create",
            "ai.sessions.read",
            "ai.interactions.create",
        }
    )
    assert actor.user_id == user.user_id
    assert actor.firebase_uid == user.firebase_uid
    assert actor.farm_ids == tuple(farm.farm_id for farm in user.farms)


@pytest.mark.asyncio
async def test_principal_rejects_token_when_firebase_uid_changed_on_user_record() -> None:
    codec = _codec()
    user = _user(firebase_uid="new-firebase-uid")
    token = codec.issue(user.user_id, "old-firebase-uid")

    with pytest.raises(HTTPException) as error:
        await _principal_from_access_token(_principal_request(codec, token, user), token)

    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"permission_active": False},
        {"blocked_until": datetime.max.replace(tzinfo=UTC)},
        {"blocked_until": datetime.max},
        {"farms": ()},
    ],
)
@pytest.mark.asyncio
async def test_principal_rejects_disabled_cadastre_with_matching_token(overrides) -> None:
    codec = _codec()
    token_user = _user()
    token = codec.issue(token_user.user_id, token_user.firebase_uid)
    user = replace(token_user, **overrides)

    with pytest.raises(HTTPException) as error:
        await _principal_from_access_token(_principal_request(codec, token, user), token)

    assert error.value.status_code == 403
    assert error.value.detail == "Usuário sem cadastro habilitado ou sem fazendas autorizadas"


@pytest.mark.asyncio
async def test_principal_rejects_missing_user_with_401() -> None:
    codec = _codec()
    user = _user()
    token = codec.issue(user.user_id, user.firebase_uid)
    with pytest.raises(HTTPException) as error:
        await _principal_from_access_token(_principal_request(codec, token, None), token)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_principal_rejects_reassociated_uid_with_different_internal_id() -> None:
    codec = _codec()
    user = _user()
    token = codec.issue(user.user_id, user.firebase_uid)
    reassociated_user = replace(user, user_id=uuid4())
    with pytest.raises(HTTPException) as error:
        await _principal_from_access_token(
            _principal_request(codec, token, reassociated_user), token
        )
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_principal_returns_503_on_database_failure() -> None:
    from unittest.mock import AsyncMock

    codec = _codec()
    user = _user()
    token = codec.issue(user.user_id, user.firebase_uid)
    request = _principal_request(codec, token, user)
    request.app.state.user_access_repository.find_by_firebase_uid = AsyncMock(
        side_effect=TimeoutError("internal connection details")
    )
    with pytest.raises(HTTPException) as error:
        await _principal_from_access_token(request, token)
    assert error.value.status_code == 503
    assert "internal connection details" not in error.value.detail


@pytest.mark.parametrize("role", ["ADMIN", "GERENTE", "PECUARISTA", "APLICADOR", "SISTEMA"])
@pytest.mark.asyncio
async def test_enabled_roles_share_operations_and_only_current_farms(role) -> None:
    codec = _codec()
    user = replace(_user(), permission_code=role)
    token = codec.issue(user.user_id, user.firebase_uid)
    principal = await _principal_from_access_token(_principal_request(codec, token, user), token)
    assert principal.permission == role
    assert principal.farm_ids == tuple(farm.farm_id for farm in user.farms)
    assert principal.operations == {
        "ai.sessions.create",
        "ai.sessions.read",
        "ai.interactions.create",
    }


@pytest.mark.asyncio
async def test_expired_block_allows_same_identity() -> None:
    codec = _codec()
    user = replace(_user(), blocked_until=datetime.now(UTC) - timedelta(seconds=1))
    token = codec.issue(user.user_id, user.firebase_uid)
    principal = await _principal_from_access_token(_principal_request(codec, token, user), token)
    assert principal.user_id == user.user_id
