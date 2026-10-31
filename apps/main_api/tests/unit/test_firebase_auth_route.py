"""Contrato HTTP real da troca e das chamadas protegidas, com Firebase/DB simulados."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from app.api.v1.dependencies.auth import PrincipalDep
from app.api.v1.routes.auth import router
from app.infrastructure.firebase_auth import (
    FirebaseIdentity,
    FirebaseIdentityAuthenticator,
    FirebaseVerificationUnavailable,
    InvalidFirebaseIdToken,
)
from app.settings import Settings
from fastapi import FastAPI


@pytest.fixture
def auth_http_app(auth_user, auth_codec):
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    users = SimpleNamespace(find_by_firebase_uid=AsyncMock(return_value=auth_user))
    verifier = Mock()
    verifier.verify.return_value = FirebaseIdentity(auth_user.firebase_uid, None, False)
    app.state.settings = Settings()
    app.state.access_token_codec = auth_codec
    app.state.user_access_repository = users
    app.state.firebase_identity_authenticator = FirebaseIdentityAuthenticator(verifier, users)

    @app.get("/protected")
    async def protected(principal: PrincipalDep):
        return {
            "user_id": str(principal.user_id),
            "name": principal.name,
            "email": principal.email,
            "farm_ids": [str(farm) for farm in principal.farm_ids],
        }

    return app, users, verifier


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_exchange_returns_only_jwt_and_builds_server_identity(
    auth_http_app, auth_user, auth_codec
):
    app, users, verifier = auth_http_app
    async with _client(app) as client:
        response = await client.post(
            "/v1/auth/firebase",
            headers={
                "Authorization": "Bearer firebase-id-token",
                "X-User-Id": str(uuid4()),
                "X-Farm-Ids": str(uuid4()),
            },
        )
        assert response.status_code == 200
        assert set(response.json()) == {"access_token"}
        token = response.json()["access_token"]
        claims = auth_codec.verify(token)
        assert claims.user_id == auth_user.user_id
        assert claims.firebase_uid == auth_user.firebase_uid
        result = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200
    assert result.json() == {
        "user_id": str(auth_user.user_id),
        "name": auth_user.name,
        "email": auth_user.email,
        "farm_ids": [str(farm.farm_id) for farm in auth_user.farms],
    }
    verifier.verify.assert_called_once_with("firebase-id-token")
    assert users.find_by_firebase_uid.await_count == 2


@pytest.mark.parametrize("authorization", [None, "", "Basic token", "Bearer "])
@pytest.mark.asyncio
async def test_exchange_rejects_missing_bearer_before_dependencies(auth_http_app, authorization):
    app, users, verifier = auth_http_app
    headers = {} if authorization is None else {"Authorization": authorization}
    async with _client(app) as client:
        response = await client.post("/v1/auth/firebase", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    verifier.verify.assert_not_called()
    users.find_by_firebase_uid.assert_not_awaited()


@pytest.mark.parametrize(
    "failure,expected",
    [
        (InvalidFirebaseIdToken("private token detail"), 401),
        (FirebaseVerificationUnavailable("private SDK detail"), 503),
    ],
)
@pytest.mark.asyncio
async def test_exchange_handles_firebase_rejection_and_outage(auth_http_app, failure, expected):
    app, users, verifier = auth_http_app
    verifier.verify.side_effect = failure
    async with _client(app) as client:
        response = await client.post("/v1/auth/firebase", headers={"Authorization": "Bearer test"})
    assert response.status_code == expected
    assert set(response.json()) == {"detail"}
    assert "private" not in response.text
    users.find_by_firebase_uid.assert_not_awaited()


@pytest.mark.parametrize(
    "overrides",
    [
        None,
        {"active": False},
        {"permission_active": False},
        {"farms": ()},
        {"blocked_until": datetime.now(UTC) + timedelta(days=1)},
    ],
)
@pytest.mark.asyncio
async def test_exchange_refuses_missing_or_disabled_cadastre(auth_http_app, auth_user, overrides):
    app, users, _ = auth_http_app
    users.find_by_firebase_uid.return_value = (
        None if overrides is None else replace(auth_user, **overrides)
    )
    async with _client(app) as client:
        response = await client.post("/v1/auth/firebase", headers={"Authorization": "Bearer test"})
    assert response.status_code == 403
    assert "access_token" not in response.json()


@pytest.mark.parametrize("path", ["/v1/auth/firebase", "/protected"])
@pytest.mark.asyncio
async def test_database_outage_fails_closed_with_503(auth_http_app, auth_user, auth_codec, path):
    app, users, _ = auth_http_app
    users.find_by_firebase_uid.side_effect = TimeoutError("private database detail")
    token = auth_codec.issue(auth_user.user_id, auth_user.firebase_uid)
    async with _client(app) as client:
        response = await client.request(
            "POST" if path.endswith("firebase") else "GET",
            path,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 503
    assert "private" not in response.text


@pytest.mark.parametrize("dependency", ["firebase_identity_authenticator", "access_token_codec"])
@pytest.mark.asyncio
async def test_exchange_requires_configuration(auth_http_app, dependency):
    app, _, _ = auth_http_app
    setattr(app.state, dependency, None)
    async with _client(app) as client:
        response = await client.post("/v1/auth/firebase", headers={"Authorization": "Bearer test"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_expired_jwt_can_be_exchanged_again_without_changing_identity(
    auth_http_app, auth_clock, auth_user, auth_codec
):
    """Somente as trocas consultam Firebase."""
    app, users, verifier = auth_http_app
    async with _client(app) as client:
        first = await client.post("/v1/auth/firebase", headers={"Authorization": "Bearer first"})
        old_token = first.json()["access_token"]
        auth_clock[0] += 300
        calls = users.find_by_firebase_uid.await_count
        expired = await client.get("/protected", headers={"Authorization": f"Bearer {old_token}"})
        assert expired.status_code == 401
        assert users.find_by_firebase_uid.await_count == calls
        renewed = await client.post("/v1/auth/firebase", headers={"Authorization": "Bearer new"})
        assert renewed.status_code == 200
        new_token = renewed.json()["access_token"]
        assert new_token != old_token
        assert auth_codec.verify(new_token).user_id == auth_user.user_id
        result = await client.get("/protected", headers={"Authorization": f"Bearer {new_token}"})
    assert result.status_code == 200
    assert verifier.verify.call_count == 2


@pytest.mark.parametrize(
    "overrides,status",
    [
        ({"farms": ()}, 403),
        ({"active": False}, 403),
        ({"permission_active": False}, 403),
        ({"user_id": uuid4()}, 401),
    ],
)
@pytest.mark.asyncio
async def test_current_database_state_overrides_still_valid_jwt(
    auth_http_app, auth_user, auth_codec, overrides, status
):
    """Token ainda não expirou."""
    app, users, verifier = auth_http_app
    token = auth_codec.issue(auth_user.user_id, auth_user.firebase_uid)
    async with _client(app) as client:
        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/protected", headers=headers)).status_code == 200
        users.find_by_firebase_uid.return_value = replace(auth_user, **overrides)
        assert (await client.get("/protected", headers=headers)).status_code == status
    assert auth_codec.verify(token).user_id == auth_user.user_id
    verifier.verify.assert_not_called()


def test_auth_contract_has_no_request_body_or_logout(auth_http_app):
    app, _, _ = auth_http_app
    schema = app.openapi()
    assert "requestBody" not in schema["paths"]["/v1/auth/firebase"]["post"]
    assert set(schema["components"]["schemas"]["AccessTokenResponse"]["properties"]) == {
        "access_token"
    }
    assert "/v1/auth/logout" not in schema["paths"]


@pytest.mark.asyncio
async def test_firebase_revocation_blocks_exchange_but_existing_jwt_lasts_until_expiration(
    auth_http_app, auth_clock
):
    app, _, verifier = auth_http_app
    async with _client(app) as client:
        exchange = await client.post(
            "/v1/auth/firebase", headers={"Authorization": "Bearer original-id-token"}
        )
        token = exchange.json()["access_token"]
        verifier.verify.side_effect = InvalidFirebaseIdToken("Firebase revoked")
        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/protected", headers=headers)).status_code == 200
        rejected = await client.post(
            "/v1/auth/firebase", headers={"Authorization": "Bearer revoked-id-token"}
        )
        assert rejected.status_code == 401
        auth_clock[0] += 300
        assert (await client.get("/protected", headers=headers)).status_code == 401
    assert verifier.verify.call_count == 2


@pytest.mark.asyncio
async def test_current_farm_scope_is_reloaded_without_issuing_new_jwt(
    auth_http_app, auth_user, auth_codec
):
    app, users, _ = auth_http_app
    token = auth_codec.issue(auth_user.user_id, auth_user.firebase_uid)
    new_farm = replace(auth_user.farms[0], farm_id=uuid4())
    users.find_by_firebase_uid.return_value = replace(auth_user, farms=(new_farm,))
    async with _client(app) as client:
        response = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["farm_ids"] == [str(new_farm.farm_id)]
