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
            access_token_ttl_seconds=900,
        ),
        clock=clock,
    )


def test_access_token_contains_only_internal_subject_and_standard_claims() -> None:
    user_id = uuid4()
    codec = _codec()

    token = codec.issue(user_id)
    claims = codec.verify(token)

    assert claims.user_id == user_id
    assert claims.token_id
    payload = token.split(".")[1]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    assert set(json.loads(decoded)) == {"iss", "aud", "sub", "jti", "iat", "nbf", "exp", "v"}


def test_access_token_rejects_modified_signature() -> None:
    codec = _codec()
    token = codec.issue(uuid4())
    header, payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"

    with pytest.raises(ValueError):
        codec.verify(f"{header}.{payload}.{replacement}{signature[1:]}")


def test_access_token_expires_at_configured_ttl() -> None:
    now = [1_000]
    codec = _codec(clock=lambda: now[0])
    token = codec.issue(uuid4())
    now[0] = 1_900

    with pytest.raises(ValueError, match="expirado"):
        codec.verify(token)
