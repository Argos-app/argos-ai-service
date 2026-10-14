"""Testes do contrato de tokens de delegação emitidos pelo main_api."""

import base64
from uuid import uuid4

import pytest
from app.infrastructure.mcp.delegation import (
    DelegationRequestError,
    McpDelegationTokenIssuer,
)
from app.settings import Settings
from argos_mcp_data.adapters.delegation import DelegationTokenVerifier as DataTokenVerifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.observability import bind_correlation, observation_scope
from packages.security.delegation import DelegationTokenVerifier as PredictionTokenVerifier


@pytest.fixture
def delegation_keys() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_der).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def test_issued_data_token_matches_mcp_verifier_contract(delegation_keys):
    private_key, public_key = delegation_keys
    data_url = "https://data.example/mcp"
    issuer = McpDelegationTokenIssuer(
        Settings(
            mcp_data_url=data_url,
            mcp_delegation_key_id="main-key",
            mcp_delegation_private_key=private_key,
            mcp_delegation_ttl_seconds=240,
        ),
        clock=lambda: 1_800_000_000,
    )
    verifier = DataTokenVerifier(
        {"main-key": public_key},
        issuer="argos-main-service",
        audience=data_url,
        clock=lambda: 1_800_000_010,
    )
    user_id, property_id, request_id = uuid4(), uuid4(), uuid4()
    correlation_id, parent_observation_id = uuid4(), uuid4()

    with bind_correlation(correlation_id), observation_scope(parent_observation_id):
        token = issuer.issue(
            service="data",
            user_id=user_id,
            authorized_property_ids=(property_id,),
            agent="fazenda",
            operation="cattle.read",
            request_id=request_id,
        )

    context = verifier.verify(token)
    assert context.user_id == user_id
    assert context.authorized_property_ids == (property_id,)
    assert context.agent_id == "fazenda"
    assert context.operations == frozenset({"cattle.read"})
    assert context.request_id == str(request_id)
    assert context.correlation_id == correlation_id
    assert context.parent_observation_id == parent_observation_id
    assert (context.expires_at - context.issued_at).total_seconds() == 240


def test_issued_prediction_token_matches_shared_verifier_contract(delegation_keys):
    private_key, public_key = delegation_keys
    prediction_url = "https://prediction.example/mcp"
    issuer = McpDelegationTokenIssuer(
        Settings(
            mcp_prediction_url=prediction_url,
            mcp_delegation_key_id="main-key",
            mcp_delegation_private_key=private_key,
        ),
        clock=lambda: 1_800_000_000,
    )
    verifier = PredictionTokenVerifier(
        {"main-key": public_key},
        issuer="argos-main-service",
        audience=prediction_url,
        agent_operations={"analista": frozenset({"prediction.read"})},
        clock=lambda: 1_800_000_010,
    )

    token = issuer.issue(
        service="prediction",
        user_id=uuid4(),
        authorized_property_ids=(uuid4(),),
        agent="analista",
        operation="prediction.read",
        request_id=uuid4(),
    )

    context = verifier.verify(token)
    assert context.agent_id == "analista"
    assert context.operations == frozenset({"prediction.read"})


def test_issuer_rejects_operation_outside_static_agent_policy(delegation_keys):
    private_key, _ = delegation_keys
    issuer = McpDelegationTokenIssuer(
        Settings(
            mcp_prediction_url="https://prediction.example/mcp",
            mcp_delegation_key_id="main-key",
            mcp_delegation_private_key=private_key,
        )
    )

    with pytest.raises(DelegationRequestError):
        issuer.issue(
            service="prediction",
            user_id=uuid4(),
            authorized_property_ids=(uuid4(),),
            agent="analista",
            operation="cattle.read",
            request_id=uuid4(),
        )


def test_issuer_rejects_empty_property_scope(delegation_keys):
    private_key, _ = delegation_keys
    issuer = McpDelegationTokenIssuer(
        Settings(
            mcp_data_url="https://data.example/mcp",
            mcp_delegation_key_id="main-key",
            mcp_delegation_private_key=private_key,
        )
    )

    with pytest.raises(DelegationRequestError):
        issuer.issue(
            service="data",
            user_id=uuid4(),
            authorized_property_ids=(),
            agent="fazenda",
            operation="cattle.read",
            request_id=uuid4(),
        )
