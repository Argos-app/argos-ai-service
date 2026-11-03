import base64
import json
import time
from collections.abc import Callable
from uuid import uuid4

import pytest
from app.infrastructure.access_tokens import MainApiAccessTokenCodec
from app.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _codec(*, clock: Callable[[], float] = time.time) -> MainApiAccessTokenCodec:
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
            access_token_key_id="test-key",
            access_token_private_key=encoded_key,
            access_token_ttl_seconds=300,
        ),
        clock=clock,
    )


def test_access_token_contains_only_internal_subject_and_standard_claims() -> None:
    user_id = uuid4()
    codec = _codec()

    token = codec.issue(user_id, "firebase-user-123")
    claims = codec.verify(token)

    assert claims.user_id == user_id
    assert claims.firebase_uid == "firebase-user-123"
    assert claims.token_id
    header = token.split(".")[0]
    decoded_header = base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))
    assert json.loads(decoded_header) == {"alg": "EdDSA", "kid": "test-key", "typ": "JWT"}
    payload = token.split(".")[1]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    assert set(json.loads(decoded)) == {
        "iss",
        "aud",
        "sub",
        "firebase_uid",
        "jti",
        "iat",
        "nbf",
        "exp",
        "v",
    }
    assert json.loads(decoded)["exp"] - json.loads(decoded)["iat"] == 300
    assert "email" not in json.loads(decoded)
    assert "name" not in json.loads(decoded)
    assert "permissions" not in json.loads(decoded)
    assert "farm_ids" not in json.loads(decoded)


def test_access_token_rejects_modified_signature() -> None:
    codec = _codec()
    token = codec.issue(uuid4(), "firebase-user-123")
    header, payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"

    with pytest.raises(ValueError):
        codec.verify(f"{header}.{payload}.{replacement}{signature[1:]}")


def test_access_token_expires_at_configured_ttl() -> None:
    now = [1_000]
    codec = _codec(clock=lambda: now[0])
    token = codec.issue(uuid4(), "firebase-user-123")
    now[0] = 1_300

    with pytest.raises(ValueError, match="expirado"):
        codec.verify(token)
