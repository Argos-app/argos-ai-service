import base64
import json
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.api.v1.dependencies.auth import _verify_principal
from app.settings import Settings
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _identity_token(private_key: Ed25519PrivateKey, *, user_id: UUID) -> str:
    now = int(time.time())
    header = {"alg": "EdDSA", "kid": "backend-test-key", "typ": "JWT"}
    claims = {
        "v": 1,
        "iss": "backend-test",
        "aud": "argos-ai-service",
        "sub": str(user_id),
        "email": "user@example.test",
        "name": "Usuário de teste",
        "permission": "USER",
        "farm_ids": [],
        "operations": ["ai.sessions.create", "ai.sessions.read", "ai.interactions.create"],
        "jti": str(uuid4()),
        "request_id": str(uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + 120,
        "agent": "argos-ai-service",
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = _b64url(private_key.sign(signing_input))
    return f"{encoded_header}.{encoded_claims}.{signature}"


def test_jwt_mode_uses_injected_settings_even_if_process_environment_is_stub(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("AUTH_MODE", "stub")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    settings = Settings(
        app_env="local",
        auth_mode="jwt",
        auth_issuer="backend-test",
        auth_audience="argos-ai-service",
        auth_trusted_public_keys=(("backend-test-key", base64.b64encode(public_key).decode()),),
    )
    expected_user_id = uuid4()
    state = SimpleNamespace(settings=settings, identity_token_verifier=None)
    request = SimpleNamespace(
        headers={
            "Authorization": f"Bearer {_identity_token(private_key, user_id=expected_user_id)}"
        },
        app=SimpleNamespace(state=state),
    )

    principal = _verify_principal(request)

    assert principal.user_id == expected_user_id
    assert state.identity_token_verifier is not None


def test_two_signed_users_resolve_to_distinct_principals():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    settings = Settings(
        auth_mode="jwt",
        auth_issuer="backend-test",
        auth_audience="argos-ai-service",
        auth_trusted_public_keys=(("backend-test-key", base64.b64encode(public_key).decode()),),
    )
    state = SimpleNamespace(settings=settings, identity_token_verifier=None)
    users = (uuid4(), uuid4())

    principals = tuple(
        _verify_principal(
            SimpleNamespace(
                headers={"Authorization": f"Bearer {_identity_token(private_key, user_id=user)}"},
                app=SimpleNamespace(state=state),
            )
        )
        for user in users
    )

    assert tuple(principal.user_id for principal in principals) == users
    assert principals[0].user_id != principals[1].user_id
