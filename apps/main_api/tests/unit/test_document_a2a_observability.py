from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from uuid import uuid4

from a2a.types import a2a_pb2
from app.infrastructure.a2a.client import DocumentA2AClient
from app.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.contracts.a2a import CorpusRefreshStatus, DocumentAnswerResponse
from packages.observability import ObservationStatus, bind_correlation
from packages.security.delegation import GlobalDocumentDelegationTokenVerifier


class Collector:
    def __init__(self) -> None:
        self.observations = []

    def emit(self, observation) -> None:
        self.observations.append(observation)


def run_immediate(coroutine):
    iterator = coroutine.__await__()
    while True:
        try:
            yielded = iterator.send(None)
        except StopIteration as completed:
            return completed.value
        if yielded is not None:
            raise AssertionError("fake A2A SDK não deveria suspender a corrotina")


def test_a2a_client_records_independent_call_and_keeps_protocol_result_separate() -> None:
    private_key = Ed25519PrivateKey.generate()
    private_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_key_b64 = base64.b64encode(private_der).decode("ascii")
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    endpoint = "https://documents.internal/a2a"
    settings = Settings(
        mcp_delegation_issuer="argos-main-service",
        mcp_delegation_key_id="main-key",
        mcp_delegation_private_key=private_key_b64,
        document_a2a_url=endpoint,
        document_a2a_service_token="service-secret",
    )
    collector = Collector()
    client = DocumentA2AClient(settings, observation_collector=collector)
    user_id = uuid4()
    correlation_id = uuid4()

    class FakeClient:
        def __init__(self) -> None:
            self.request = None
            self.context = None

        async def send_message(self, request, *, context=None):
            self.request = request
            self.context = context
            request_payload = json.loads(request.message.parts[0].text)
            response = DocumentAnswerResponse(
                request_id=request_payload["request_id"],
                status="unavailable",
                limitations=("consulta indisponível",),
                refresh_status=CorpusRefreshStatus.UNAVAILABLE,
            )
            task = a2a_pb2.Task(
                id="private-task-id",
                context_id="private-context-id",
                status=a2a_pb2.TaskStatus(state=a2a_pb2.TASK_STATE_COMPLETED),
                artifacts=[
                    a2a_pb2.Artifact(
                        name="document-answer.json",
                        parts=[
                            a2a_pb2.Part(
                                text=response.model_dump_json(),
                                media_type="application/json",
                            )
                        ],
                    )
                ],
            )
            yield a2a_pb2.StreamResponse(task=task)

    fake = FakeClient()
    client._factory = SimpleNamespace(create=lambda _card: fake)
    question = "question containing private user text"

    with bind_correlation(correlation_id):
        result = json.loads(run_immediate(client.ask(question, user_id=user_id)))

    assert result["status"] == "unavailable"
    assert fake.request.message.parts[0].media_type == "application/json"
    token = fake.context.service_parameters["X-Argos-Delegation"]
    delegated = GlobalDocumentDelegationTokenVerifier(
        {"main-key": public_key_b64},
        issuer="argos-main-service",
        audience=endpoint,
    ).verify(token)
    assert delegated.user_id == user_id
    assert delegated.operations == frozenset({"documents.read"})
    assert delegated.correlation_id == correlation_id
    assert str(delegated.parent_observation_id) == collector.observations[0].id
    assert not hasattr(delegated, "authorized_property_ids")

    terminal = collector.observations[-1]
    assert terminal.status == ObservationStatus.SUCCEEDED
    assert terminal.protocol.task_state == "completed"
    assert terminal.protocol.skill == "documental"
    assert terminal.result.code == "unavailable"
    assert terminal.duration_ms is not None
    serialized_observation = repr(terminal.model_dump())
    assert question not in serialized_observation
    assert str(user_id) not in serialized_observation
    assert "private-task-id" not in serialized_observation
    assert "private-context-id" not in serialized_observation
    request_id = json.loads(fake.request.message.parts[0].text)["request_id"]
    assert request_id not in serialized_observation


def test_document_a2a_configuration_requires_endpoint_token_and_delegation_key() -> None:
    try:
        Settings(
            document_a2a_url="https://documents.internal/a2a",
            document_a2a_service_token="service-secret",
        )
    except ValueError as exc:
        assert "delegação A2A exige" in str(exc)
    else:
        raise AssertionError("configuração A2A sem chave de delegação deveria ser rejeitada")


def test_local_document_a2a_configuration_accepts_kubernetes_service_dns() -> None:
    private_key = Ed25519PrivateKey.generate()
    private_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    settings = Settings(
        app_env="local",
        mcp_delegation_key_id="main-key",
        mcp_delegation_private_key=base64.b64encode(private_der).decode("ascii"),
        document_a2a_url="http://a2a-agent:8000/a2a",
        document_a2a_service_token="service-secret",
    )

    assert settings.document_a2a_url == "http://a2a-agent:8000/a2a"
