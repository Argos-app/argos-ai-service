"""Dependências isoladas para validar a fundação sem banco de negócio."""

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from argos_mcp_prediction.settings import Settings
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def delegation_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def configured_settings(delegation_key):
    return Settings(
        sql_dsn="postgresql://readonly@localhost/prediction_test",
        internal_service_token="prediction-test-token",
        mcp_auth_issuer_url="http://127.0.0.1:8000",
        mcp_resource_server_url="http://127.0.0.1:8000/mcp",
        delegation_issuer="argos-main-service",
        trusted_delegation_keys=(
            ("test-key", base64.b64encode(delegation_key.public_key().public_bytes_raw()).decode()),
        ),
        mcp_host="127.0.0.1",
    )


@pytest.fixture
def sql_pool():
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.fetchval = AsyncMock(return_value="on")
    connection.fetch = AsyncMock(return_value=[{"value": 7}])
    transaction = connection.transaction.return_value
    transaction.__aenter__ = AsyncMock()
    transaction.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock()
    return pool, connection


@pytest.fixture
def signed_delegation(delegation_key, configured_settings):
    def sign(**overrides):
        now = int(time.time())
        claims = {
            "v": 1,
            "iss": configured_settings.delegation_issuer,
            "aud": configured_settings.mcp_resource_server_url,
            "sub": str(uuid4()),
            "authorized_property_ids": [str(uuid4())],
            "agent": "analyst",
            "operations": ["prediction.read"],
            "iat": now - 1,
            "nbf": now - 1,
            "exp": now + 120,
            "jti": "delegation-test",
            "request_id": "request-test",
        }
        claims.update(overrides)

        def encode(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

        head = encode({"alg": "EdDSA", "kid": "test-key"})
        payload = encode(claims)
        signature = delegation_key.sign(f"{head}.{payload}".encode())
        token = f"{head}.{payload}." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return token, claims

    return sign
