"""Identidade e chave descartáveis para os testes de autenticação da API."""

import base64
import time
from uuid import uuid4

import pytest
from app.domain.user_access import UserAccess, UserFarm
from app.infrastructure.access_tokens import MainApiAccessTokenCodec
from app.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def auth_user():
    return UserAccess(
        user_id=uuid4(),
        firebase_uid="firebase-auth-test-user",
        name="Pessoa cadastrada",
        email="database@example.test",
        active=True,
        blocked_until=None,
        permission_code="SISTEMA",
        permission_name="Sistema",
        permission_active=True,
        farms=(UserFarm(uuid4(), "Fazenda autorizada"),),
    )


@pytest.fixture
def auth_clock():
    return [int(time.time())]


@pytest.fixture
def auth_codec(auth_clock):
    private_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return MainApiAccessTokenCodec(
        Settings(
            access_token_key_id="auth-test-only",
            access_token_private_key=base64.b64encode(private_key).decode(),
        ),
        clock=lambda: auth_clock[0],
    )
