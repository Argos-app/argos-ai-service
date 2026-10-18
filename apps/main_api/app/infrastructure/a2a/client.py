"""Cliente A2A documental com captura bruta da fronteira de chamada."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from a2a.client import ClientCallContext, ClientConfig, ClientFactory
from a2a.types import a2a_pb2
from a2a.utils.constants import TransportProtocol
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.infrastructure.observability.recording import observe_operation
from app.settings import Settings
from packages.contracts.a2a import (
    DocumentAnswerResponse,
    DocumentAnswerStatus,
    DocumentQuestionRequest,
    DocumentSkill,
)
from packages.observability import (
    CancellationState,
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
    OperationPhase,
    ProtocolMetadata,
    ResultMetadata,
    current_correlation_id,
    current_parent_observation_id,
)

_TASK_STATES = {
    a2a_pb2.TASK_STATE_SUBMITTED: "submitted",
    a2a_pb2.TASK_STATE_WORKING: "working",
    a2a_pb2.TASK_STATE_COMPLETED: "completed",
    a2a_pb2.TASK_STATE_FAILED: "failed",
    a2a_pb2.TASK_STATE_CANCELED: "cancelled",
    a2a_pb2.TASK_STATE_INPUT_REQUIRED: "input_required",
    a2a_pb2.TASK_STATE_REJECTED: "rejected",
    a2a_pb2.TASK_STATE_AUTH_REQUIRED: "auth_required",
}


class DocumentA2ACallError(RuntimeError):
    """Falha segura ao chamar o serviço documental A2A."""


class DocumentA2AClient:
    """Envia uma pergunta documental sem registrar payload ou IDs de protocolo."""

    def __init__(
        self,
        settings: Settings,
        *,
        observation_collector: ObservationCollector | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.document_a2a_url or not settings.document_a2a_service_token:
            raise ValueError("cliente documental A2A exige endpoint e token de serviço")
        if not settings.mcp_delegation_key_id or not settings.mcp_delegation_private_key:
            raise ValueError("cliente documental A2A exige chave de delegação")
        self._endpoint = settings.document_a2a_url
        self._service_token = settings.document_a2a_service_token
        self._issuer = settings.mcp_delegation_issuer
        self._key_id = settings.mcp_delegation_key_id
        self._private_key = self._load_private_key(settings.mcp_delegation_private_key)
        self._ttl_seconds = settings.mcp_delegation_ttl_seconds
        self._timeout_seconds = settings.document_a2a_timeout_seconds
        self._response_max_bytes = settings.document_a2a_response_max_bytes
        self.observation_collector = observation_collector
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Authorization": f"Bearer {self._service_token}"},
            transport=http_transport,
        )
        self._factory = ClientFactory(
            ClientConfig(
                streaming=False,
                polling=False,
                httpx_client=self._http,
                accepted_output_modes=["application/json"],
            )
        )
        self._card = a2a_pb2.AgentCard(
            name="Argos Documentos Globais",
            supported_interfaces=[
                a2a_pb2.AgentInterface(
                    url=self._endpoint,
                    protocol_binding=TransportProtocol.JSONRPC,
                    protocol_version="1.0.0",
                )
            ],
            capabilities=a2a_pb2.AgentCapabilities(streaming=False),
            version="1.0.0",
        )

    def bind_observation_collector(self, collector: ObservationCollector) -> None:
        self.observation_collector = collector

    async def check_ready(self) -> None:
        """Confirma que o serviço A2A e seus próprios pré-requisitos estão prontos."""
        endpoint = urlsplit(self._endpoint)
        health_url = urlunsplit((endpoint.scheme, endpoint.netloc, "/health/ready", "", ""))
        response = await self._http.get(health_url, timeout=httpx.Timeout(2.0))
        if response.status_code != 200:
            raise DocumentA2ACallError("serviço documental não está pronto")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DocumentA2ACallError("healthcheck documental inválido") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ready":
            raise DocumentA2ACallError("serviço documental não está pronto")

    async def ask(
        self,
        question: str,
        *,
        user_id: str | UUID,
        skill: DocumentSkill = DocumentSkill.DOCUMENTAL,
        jurisdiction: str | None = None,
        reference_date: date | None = None,
    ) -> str:
        observe_context = (
            observe_operation(
                self.observation_collector,
                operation_kind=OperationKind.A2A,
                operation_name="a2a.documental.message.send",
                operation_phase=OperationPhase.EXECUTION,
                observation_level=ObservationLevel.TRANSPORT_ATTEMPT,
                agent=skill.value,
                timeout_ms=self._timeout_seconds * 1000,
                error_origin=ErrorOrigin.TRANSPORT,
                protocol=ProtocolMetadata(
                    name="a2a",
                    operation="message.send",
                    skill=skill.value,
                    task_state="working",
                ),
            )
            if self.observation_collector is not None
            else nullcontext(None)
        )
        async with observe_context as recorder:
            request_id = uuid4()
            request = DocumentQuestionRequest(
                request_id=request_id,
                question=question,
                skill=skill,
                jurisdiction=jurisdiction,
                reference_date=reference_date,
                deadline_at=datetime.now(UTC) + timedelta(seconds=self._timeout_seconds),
            )
            delegation = self._delegation_token(
                user_id=user_id,
                request_id=str(request_id),
            )
            message = a2a_pb2.Message(
                message_id=str(uuid4()),
                role=a2a_pb2.ROLE_USER,
                parts=[
                    a2a_pb2.Part(
                        text=request.model_dump_json(),
                        media_type="application/json",
                    )
                ],
            )
            rpc_request = a2a_pb2.SendMessageRequest(
                message=message,
                configuration=a2a_pb2.SendMessageConfiguration(
                    accepted_output_modes=["application/json"]
                ),
            )
            call_context = ClientCallContext(
                timeout=self._timeout_seconds,
                service_parameters={"X-Argos-Delegation": delegation},
            )
            client = self._factory.create(self._card)
            try:
                events = [
                    event async for event in client.send_message(rpc_request, context=call_context)
                ]
                task = next(
                    (event.task for event in reversed(events) if event.HasField("task")),
                    None,
                )
                if task is None:
                    raise DocumentA2ACallError("serviço documental não retornou uma tarefa")

                task_state = _TASK_STATES.get(task.status.state, "unknown")
                protocol = ProtocolMetadata(
                    name="a2a",
                    operation="message.send",
                    skill=skill.value,
                    task_state=task_state,
                )
                if task_state == "rejected":
                    if recorder is not None:
                        recorder.finish(
                            status=ObservationStatus.REJECTED,
                            protocol=protocol,
                            result=ResultMetadata(source="document_answer", code="rejected"),
                        )
                    return _unavailable_result("a solicitação foi rejeitada pelo serviço")
                if task_state == "cancelled":
                    if recorder is not None:
                        recorder.finish(
                            status=ObservationStatus.CANCELLED,
                            cancellation_state=CancellationState.CONFIRMED,
                            protocol=protocol,
                            result=ResultMetadata(source="document_answer", code="cancelled"),
                        )
                    return _unavailable_result("a tarefa foi cancelada pelo serviço")
                if task_state != "completed":
                    if recorder is not None:
                        recorder.finish(
                            status=ObservationStatus.FAILED,
                            protocol=protocol,
                            result=ResultMetadata(source="document_answer", code="task_incomplete"),
                        )
                    return _unavailable_result("o serviço não concluiu a tarefa")

                response = self._answer_from_task(task, request_id)
                if recorder is not None:
                    recorder.finish(
                        protocol=protocol,
                        result=ResultMetadata(source="document_answer", code=response.status.value),
                    )
                return response.model_dump_json()
            except BaseException as exc:
                if recorder is not None:
                    recorder.finish(error=exc)
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                if isinstance(exc, DocumentA2ACallError):
                    raise
                raise DocumentA2ACallError(
                    "serviço documental temporariamente indisponível"
                ) from exc

    def _answer_from_task(self, task: Any, request_id: UUID) -> DocumentAnswerResponse:
        for artifact in reversed(task.artifacts):
            for part in artifact.parts:
                if not part.text:
                    continue
                if len(part.text.encode("utf-8")) > self._response_max_bytes:
                    raise DocumentA2ACallError("resposta documental excede o limite configurado")
                try:
                    response = DocumentAnswerResponse.model_validate_json(part.text)
                except ValueError:
                    continue
                if response.request_id != request_id:
                    raise DocumentA2ACallError("resposta documental não corresponde à chamada")
                return response
        raise DocumentA2ACallError("tarefa concluída sem artefato documental válido")

    def _delegation_token(self, *, user_id: str | UUID, request_id: str) -> str:
        try:
            subject = str(UUID(str(user_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise DocumentA2ACallError("identidade autenticada inválida para delegação") from exc
        issued_at = int(time.time())
        header = {"alg": "EdDSA", "kid": self._key_id, "typ": "JWT"}
        claims: dict[str, Any] = {
            "v": 1,
            "iss": self._issuer,
            "aud": self._endpoint,
            "sub": subject,
            "agent": "informacoes",
            "operations": ["documents.read"],
            "request_id": request_id,
            "correlation_id": str(current_correlation_id() or request_id),
            "parent_observation_id": (
                str(current_parent_observation_id())
                if current_parent_observation_id() is not None
                else None
            ),
            "jti": str(uuid4()),
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + self._ttl_seconds,
        }
        encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
        encoded_claims = _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = self._private_key.sign(signing_input)
        return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"

    @staticmethod
    def _load_private_key(value: str) -> Ed25519PrivateKey:
        try:
            encoded = base64.b64decode(value, validate=True)
            key = serialization.load_der_private_key(encoded, password=None)
        except (ValueError, TypeError) as exc:
            raise ValueError("chave de delegação A2A deve ser PKCS#8 DER Base64") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("chave de delegação A2A deve usar Ed25519")
        return key

    async def aclose(self) -> None:
        await self._http.aclose()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unavailable_result(reason: str) -> str:
    return json.dumps(
        {
            "status": DocumentAnswerStatus.UNAVAILABLE.value,
            "limitations": [reason],
            "sources": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
