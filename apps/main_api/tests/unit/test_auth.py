import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.dependencies.auth import _verify_access_token
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
