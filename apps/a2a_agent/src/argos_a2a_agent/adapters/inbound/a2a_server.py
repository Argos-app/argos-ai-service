"""Adapters do protocolo A2A sobre o SDK oficial."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.routes import DefaultServerCallContextBuilder
from a2a.server.tasks import TaskUpdater
from a2a.types import a2a_pb2
from google.protobuf.timestamp_pb2 import Timestamp
from pydantic import ValidationError

from argos_a2a_agent.adapters.inbound.delegation import ACCESS_CONTEXT_KEY
from argos_a2a_agent.adapters.observability.recording import OperationRecorder
from argos_a2a_agent.agents.registry import DocumentAgentRegistry
from packages.contracts.a2a import DocumentAnswerResponse, DocumentQuestionRequest
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
)
from packages.security.access import DocumentAccessContext
from packages.security.errors import AccessContextError


class DocumentA2AExecutor(AgentExecutor):
    """Valida o envelope estruturado e publica a resposta como artifact JSON."""

    def __init__(
        self,
        registry: DocumentAgentRegistry,
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        self._registry = registry
        self._observation_collector = observation_collector

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise ValueError("o SDK não forneceu IDs de task e contexto")

        updater = TaskUpdater(event_queue, task_id, context_id)
        recorder = (
            OperationRecorder(
                self._observation_collector,
                operation_kind=OperationKind.A2A,
                operation_name="a2a.task.execute",
                operation_phase=OperationPhase.EXECUTION,
                observation_level=ObservationLevel.TRANSPORT_ATTEMPT,
                protocol=ProtocolMetadata(
                    name="a2a", operation="task.execute", task_state="working"
                ),
                error_origin=ErrorOrigin.PROTOCOL,
            )
            if self._observation_collector is not None
            else None
        )
        try:
            await self._execute_task(context, event_queue, updater, recorder)
        except BaseException as exc:
            if recorder is not None:
                task_state = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                recorder.finish(
                    error=exc,
                    protocol=ProtocolMetadata(
                        name="a2a", operation="task.execute", task_state=task_state
                    ),
                )
            raise

    async def _execute_task(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        updater: TaskUpdater,
        recorder: OperationRecorder | None,
    ) -> None:
        if context.current_task is None:
            timestamp = Timestamp()
            timestamp.FromDatetime(datetime.now(UTC))
            await event_queue.enqueue_event(
                a2a_pb2.Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=a2a_pb2.TaskStatus(
                        state=a2a_pb2.TASK_STATE_SUBMITTED,
                        timestamp=timestamp,
                    ),
                )
            )
        await updater.start_work()

        try:
            message = context.message
            if message is None or len(message.parts) != 1:
                raise ValueError("uma parte JSON é obrigatória")
            part = message.parts[0]
            if part.WhichOneof("content") != "text" or not part.text:
                raise ValueError("a pergunta deve ser enviada como texto JSON")
            if part.media_type and part.media_type != "application/json":
                raise ValueError("media type não suportado")
            request = DocumentQuestionRequest.model_validate_json(part.text)
        except ValidationError, ValueError:
            await updater.reject(
                updater.new_agent_message(
                    [
                        a2a_pb2.Part(
                            text="Envie uma pergunta no formato JSON do contrato documental."
                        )
                    ]
                )
            )
            if recorder is not None:
                recorder.finish(
                    status=ObservationStatus.REJECTED,
                    protocol=ProtocolMetadata(
                        name="a2a", operation="task.execute", task_state="rejected"
                    ),
                    result=ResultMetadata(source="a2a", code="invalid_request"),
                )
            return

        access_context = context.call_context.state.get(ACCESS_CONTEXT_KEY)
        if not isinstance(access_context, DocumentAccessContext):
            await updater.reject(
                updater.new_agent_message(
                    [a2a_pb2.Part(text="Contexto de acesso documental obrigatório.")]
                )
            )
            if recorder is not None:
                recorder.finish(
                    status=ObservationStatus.REJECTED,
                    protocol=ProtocolMetadata(
                        name="a2a", operation="task.execute", task_state="rejected"
                    ),
                    result=ResultMetadata(source="a2a", code="access_context_invalid"),
                )
            return
        try:
            response = await self._registry.execute(request, access_context)
        except Exception:
            # Não retornar mensagens internas, tokens, fontes parciais ou exceções
            # do provedor ao chamador do protocolo.
            response = DocumentAnswerResponse(
                request_id=request.request_id,
                status="unavailable",
                limitations=("O serviço documental não conseguiu concluir a consulta.",),
                refresh_status="unavailable",
            )

        await updater.add_artifact(
            [
                a2a_pb2.Part(
                    text=json.dumps(
                        response.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    media_type="application/json",
                )
            ],
            name="document-answer.json",
            metadata={"content_type": "application/json"},
        )
        await updater.complete()
        if recorder is not None:
            recorder.finish(
                protocol=ProtocolMetadata(
                    name="a2a", operation="task.execute", task_state="completed"
                ),
                result=ResultMetadata(source="document_answer", code=response.status.value),
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """A execução é cancelável pelo próprio SDK e não possui efeitos de escrita."""

        if context.task_id and context.context_id:
            updater = TaskUpdater(event_queue, context.task_id, context.context_id)
            recorder = (
                OperationRecorder(
                    self._observation_collector,
                    operation_kind=OperationKind.A2A,
                    operation_name="a2a.task.cancel",
                    operation_phase=OperationPhase.EXECUTION,
                    observation_level=ObservationLevel.TRANSPORT_ATTEMPT,
                    protocol=ProtocolMetadata(
                        name="a2a", operation="task.cancel", task_state="cancel_requested"
                    ),
                    error_origin=ErrorOrigin.PROTOCOL,
                )
                if self._observation_collector is not None
                else None
            )
            try:
                await updater.cancel()
            except BaseException as exc:
                if recorder is not None:
                    recorder.finish(error=exc)
                raise
            if recorder is not None:
                recorder.finish(
                    cancellation_state=CancellationState.CONFIRMED,
                    protocol=ProtocolMetadata(
                        name="a2a", operation="task.cancel", task_state="cancelled"
                    ),
                    result=ResultMetadata(source="a2a", code="cancel_confirmed"),
                )


class DocumentCallContextBuilder(DefaultServerCallContextBuilder):
    """Propaga somente o contexto delegado verificado para o ciclo A2A."""

    def build(self, request: Any) -> ServerCallContext:
        context = super().build(request)
        verified = request.scope.get("state", {}).get(ACCESS_CONTEXT_KEY)
        if isinstance(verified, DocumentAccessContext):
            context.state[ACCESS_CONTEXT_KEY] = verified
        return context


def create_agent_card(public_url: str) -> Any:
    """Card público descrevendo apenas as duas skills especialistas."""

    from a2a.types import AgentCard, AgentSkill, HTTPAuthSecurityScheme, SecurityRequirement

    security_scheme = "serviceBearer"
    required_auth = SecurityRequirement(schemes={security_scheme: []})
    return AgentCard(
        name="Argos Documentos Globais",
        description=(
            "Consulta somente leitura de leis e termos/condições globais, com fontes "
            "rastreáveis e revisão atual do corpus."
        ),
        supported_interfaces=[
            a2a_pb2.AgentInterface(
                url=public_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0.0",
            )
        ],
        version="1.0.0",
        capabilities=a2a_pb2.AgentCapabilities(streaming=False, push_notifications=False),
        security_schemes={
            security_scheme: a2a_pb2.SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="Bearer",
                    description=(
                        "Token interno do serviço; cada operação também exige "
                        "X-Argos-Delegation válido."
                    ),
                )
            )
        },
        security_requirements=[required_auth],
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="documental",
                name="Documental",
                description="Consulta termos e condições globais com evidências documentais.",
                tags=["termos", "condições", "documentos", "consulta"],
                examples=["Quais são as condições previstas neste termo?"],
                input_modes=["application/json"],
                output_modes=["application/json"],
                security_requirements=[required_auth],
            ),
            AgentSkill(
                id="legal",
                name="Legal",
                description="Consulta leis globais com referências de fonte e página.",
                tags=["leis", "legislação", "jurisdição", "consulta"],
                examples=["O que a lei estabelece para esta jurisdição?"],
                input_modes=["application/json"],
                output_modes=["application/json"],
                security_requirements=[required_auth],
            ),
        ],
    )


def task_owner(context: Any) -> str:
    """Separa tasks no armazenamento do SDK pelo usuário da delegação verificada."""

    access_context = context.state.get(ACCESS_CONTEXT_KEY)
    if not isinstance(access_context, DocumentAccessContext):
        raise AccessContextError("contexto documental obrigatório para acessar tasks")
    return f"document-user:{access_context.user_id}"
