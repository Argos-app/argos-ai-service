import base64
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
from app.infrastructure.firebase_auth import (
    FirebaseAdminIdTokenVerifier,
    FirebaseVerificationUnavailable,
)
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
    def __init__(self, user: UserAccess) -> None:
        self.user = user

    async def find_by_user_id(self, user_id):
        return self.user if user_id == self.user.user_id else None


class _FirebaseAccessStatus:
    def __init__(self, revoked: bool = False) -> None:
        self.revoked = revoked
        self.checked: tuple[str, int] | None = None

    async def is_access_token_revoked(self, firebase_uid: str, issued_at: int) -> bool:
        self.checked = (firebase_uid, issued_at)
        return self.revoked


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


def _principal_request(codec, token, user, firebase_status):
    state = SimpleNamespace(
        access_token_codec=codec,
        firebase_identity_authenticator=firebase_status,
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
    firebase_status = _FirebaseAccessStatus()
    token = codec.issue(user.user_id, user.firebase_uid)

    principal = await _principal_from_access_token(
        _principal_request(codec, token, user, firebase_status), token
    )
    actor = to_actor_context(principal)

    assert principal.operations == frozenset(
        {
            "ai.sessions.create",
            "ai.sessions.read",
            "ai.interactions.create",
        }
    )
    assert actor.user_id == user.user_id
    assert actor.farm_ids == tuple(farm.farm_id for farm in user.farms)
    assert firebase_status.checked is not None
    assert firebase_status.checked[0] == user.firebase_uid


@pytest.mark.asyncio
async def test_principal_rejects_token_when_firebase_uid_changed_on_user_record() -> None:
    codec = _codec()
    user = _user(firebase_uid="new-firebase-uid")
    token = codec.issue(user.user_id, "old-firebase-uid")

    with pytest.raises(HTTPException) as error:
        await _principal_from_access_token(
            _principal_request(codec, token, user, _FirebaseAccessStatus()), token
        )

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_principal_rejects_firebase_revoked_token() -> None:
    codec = _codec()
    user = _user()
    token = codec.issue(user.user_id, user.firebase_uid)

    with pytest.raises(HTTPException) as error:
        await _principal_from_access_token(
            _principal_request(codec, token, user, _FirebaseAccessStatus(revoked=True)),
            token,
        )

    assert error.value.status_code == 401
    assert error.value.detail == "Bearer revogado"


def test_firebase_revocation_cutoff_rejects_access_token_issued_before_revocation() -> None:
    class FirebaseError(Exception):
        pass

    class UserNotFoundError(FirebaseError):
        pass

    record = SimpleNamespace(
        uid="firebase-user-123",
        disabled=False,
        tokens_valid_after_timestamp=1_000_001,
    )
    auth_module = SimpleNamespace(
        get_user=lambda _uid, app: record,
        UserNotFoundError=UserNotFoundError,
    )
    verifier = FirebaseAdminIdTokenVerifier(
        object(), auth_module=auth_module, firebase_error_type=FirebaseError
    )

    assert verifier.is_access_token_revoked("firebase-user-123", 1_000) is True
    assert verifier.is_access_token_revoked("firebase-user-123", 1_001) is False


def test_firebase_unavailability_fails_closed_during_revocation_check() -> None:
    class FirebaseError(Exception):
        pass

    class UserNotFoundError(FirebaseError):
        pass

    def unavailable(_uid, app):
        raise FirebaseError("Firebase indisponível")

    auth_module = SimpleNamespace(
        get_user=unavailable,
        UserNotFoundError=UserNotFoundError,
    )
    verifier = FirebaseAdminIdTokenVerifier(
        object(), auth_module=auth_module, firebase_error_type=FirebaseError
    )

    with pytest.raises(FirebaseVerificationUnavailable):
        verifier.is_access_token_revoked("firebase-user-123", 1_000)
