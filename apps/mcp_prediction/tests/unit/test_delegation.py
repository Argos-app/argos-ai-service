"""Validação de delegação e redução do escopo por propriedade."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_prediction.adapters.delegation import get_access_context
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.security.access import AccessContext, AuthorizedScope
from packages.security.delegation import DelegationTokenVerifier
from packages.security.errors import AccessContextError, AccessDeniedError


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_verifier_and_token(*, overrides: dict[str, object] | None = None):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    now = 1_800_000_000
    property_id = str(uuid4())
    claims: dict[str, object] = {
        "v": 1,
        "iss": "argos-main-service",
        "aud": "https://prediction.example.test/mcp",
        "sub": str(uuid4()),
        "authorized_property_ids": [property_id],
        "operations": ["prediction.read"],
        "agent": "analista",
        "iat": now - 1,
        "nbf": now - 1,
        "exp": now + 120,
        "jti": "delegation-123",
        "request_id": "request-123",
    }
    claims.update(overrides or {})
    header = b64url(json.dumps({"alg": "EdDSA", "kid": "test-key"}).encode())
    payload = b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    token = f"{header}.{payload}.{b64url(private_key.sign(signing_input))}"
    verifier = DelegationTokenVerifier(
        {"test-key": base64.b64encode(public_key).decode()},
        issuer="argos-main-service",
        audience="https://prediction.example.test/mcp",
        clock=lambda: now,
        agent_operations={"analista": frozenset({"prediction.read"})},
    )
    return verifier, token, claims


def test_valid_delegation_produces_immutable_access_context() -> None:
    verifier, token, claims = make_verifier_and_token()

    context = verifier.verify(token)

    assert str(context.user_id) == claims["sub"]
    assert str(context.authorized_property_ids[0]) == claims["authorized_property_ids"][0]
    assert context.operations == frozenset({"prediction.read"})
    assert context.agent_id == "analista"


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent": "fazenda"},
        {"operations": ["medicine.read"]},
        {"iat": 1_800_000_031},
        {"nbf": 1_800_000_031},
        {"exp": 1_800_000_301},
        {"v": True},
        {"iat": True},
        {"jti": ""},
        {"request_id": None},
        {"aud": "https://other.example.test/mcp"},
        {"iss": "untrusted-issuer"},
        {"exp": 1_799_999_999},
        {"agent": "doutor", "operations": ["prediction.read"]},
        {"authorized_property_ids": []},
        {"authorized_property_ids": ["not-a-uuid"]},
        {"operations": ["prediction.read", "prediction.read"]},
        {"sub": 123},
        {"sub": {}},
    ],
)
def test_invalid_claims_fail_closed(overrides: dict[str, object]) -> None:
    verifier, token, claims = make_verifier_and_token(overrides=overrides)
    assert claims

    with pytest.raises(AccessContextError):
        verifier.verify(token)


def test_signature_tampering_is_rejected() -> None:
    verifier, token, _ = make_verifier_and_token()
    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1]}.{'A' * 86}"

    with pytest.raises(AccessContextError):
        verifier.verify(tampered)


def test_deeply_nested_token_header_is_rejected() -> None:
    verifier, _, _ = make_verifier_and_token()
    header = b'{"nested":' + b"[" * 5000 + b"0" + b"]" * 5000 + b"}"

    with pytest.raises(AccessContextError):
        verifier.verify(f"{b64url(header)}.e30.AA")


def test_access_context_cannot_select_property_outside_claim_or_current_access() -> None:
    property_a, property_b = uuid4(), uuid4()
    context = AccessContext(
        user_id=uuid4(),
        authorized_property_ids=(property_a,),
        operations=frozenset({"prediction.read"}),
        agent_id="analista",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        token_id="jti",
        request_id="request",
    )

    scope = AuthorizedScope.build(
        context,
        current_property_ids={property_a, property_b},
        operation="prediction.read",
        selected_property_ids=(property_a,),
    )
    assert scope.property_ids == (property_a,)

    with pytest.raises(AccessDeniedError):
        AuthorizedScope.build(
            context,
            current_property_ids={property_a, property_b},
            operation="prediction.read",
            selected_property_ids=(property_b,),
        )

    with pytest.raises(AccessDeniedError):
        AuthorizedScope.build(
            context,
            current_property_ids={property_b},
            operation="prediction.read",
        )


def test_missing_request_context_is_denied() -> None:
    with pytest.raises(AccessContextError):
        get_access_context()
