"""Preparação, confirmação e recuperação do fluxo de e-mail da Secretária."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.agent_schemas.results import SpecialistResult, SpecialistStatus
from app.agent_schemas.synthesis import EmailDispatchEvidence
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.domain.email_preparations import PendingRecipientResolution
from app.graph.state import AgentState
from app.ports.agent import AgentId
from app.ports.email_preparation_repository import EmailPreparationRepository
from langchain_core.runnables import RunnableConfig


@dataclass(frozen=True, slots=True)
class SecretaryFlowResult:
    result: SpecialistResult
    failure_reason: str | None
    errors: tuple[str, ...]
    recipient_question: PendingRecipientResolution | None
    email_preparation_confirmed: bool
    email_action_summary: str | None
    email_dispatch_evidence: EmailDispatchEvidence | None


async def process_secretary_flow(
    state: AgentState,
    *,
    agent_id: AgentId,
    result: SpecialistResult,
    failure_reason: str | None,
    errors: list[str],
    email_preparations: EmailPreparationRepository | None,
    email_dispatch: EmailDispatchWorkflow | None,
    config: RunnableConfig | None,
    recovery_context_builder: Callable[[AgentState, dict[str, Any]], dict[str, Any]],
) -> SecretaryFlowResult:
    """Aplica verificações persistidas de destinatário, preparação e envio."""
    recipient_question: PendingRecipientResolution | None = None
    preparation_confirmed = False
    updated_errors = list(errors)
    configurable = (config or {}).get("configurable") or {}

    if agent_id == AgentId.SECRETARY and email_preparations is not None:
        try:
            recipient_question = await email_preparations.get_pending_recipient_resolution(
                principal_user_id=UUID(str(configurable["argos_user_id"])),
                session_id=UUID(state["session_id"]),
                interaction_id=UUID(state["interaction_id"]),
            )
        except Exception:
            failure_reason = "recipient_resolution_storage_unavailable"
            result = SpecialistResult(
                agent=agent_id,
                status=SpecialistStatus.ERROR,
                error=failure_reason,
            )
            updated_errors.append(failure_reason)
        if recipient_question is not None:
            if failure_reason and failure_reason in updated_errors:
                updated_errors.remove(failure_reason)
            result = SpecialistResult(
                agent=agent_id,
                status=SpecialistStatus.NEEDS_CLARIFICATION,
                answer="É necessário resolver um destinatário antes de preparar o relatório.",
                missing_fields=("recipient",),
            )
        elif "confirmed_email_preparation" in state["ordered_tasks"][
            state["current_task_index"]
        ].required_evidence and result.status in {
            SpecialistStatus.SUCCESS,
            SpecialistStatus.PARTIAL,
        }:
            try:
                preparations = await email_preparations.list_prepared_for_session(
                    principal_user_id=UUID(str(configurable["argos_user_id"])),
                    session_id=UUID(state["session_id"]),
                )
                prepared = tuple(
                    item
                    for item in preparations
                    if str(item.interaction_id) == state["interaction_id"]
                    and item.prepared_report.status.value == "prepared"
                    and item.prepared_report.expires_at > _now_utc()
                )
                if not prepared:
                    result = SpecialistResult(
                        agent=agent_id,
                        status=SpecialistStatus.ERROR,
                        answer=(
                            "A origem do relatório foi localizada, mas a preparação não foi "
                            "confirmada pelo MCP de e-mail. Nenhum e-mail foi enviado."
                        ),
                        error="email_preparation_not_confirmed",
                    )
                    updated_errors.append("secretary_preparation_not_confirmed")
                else:
                    preparation_confirmed = True
            except Exception:
                result = SpecialistResult(
                    agent=agent_id,
                    status=SpecialistStatus.ERROR,
                    error="email_preparation_verification_failed",
                )
                updated_errors.append("secretary_preparation_verification_failed")
        elif result.status in {SpecialistStatus.ERROR, SpecialistStatus.UNAVAILABLE}:
            try:
                preparations = await email_preparations.list_prepared_for_session(
                    principal_user_id=UUID(str(configurable["argos_user_id"])),
                    session_id=UUID(state["session_id"]),
                )
                prepared = tuple(
                    item
                    for item in preparations
                    if str(item.interaction_id) == state["interaction_id"]
                    and item.prepared_report.status.value == "prepared"
                    and item.prepared_report.expires_at > _now_utc()
                )
                if len(prepared) == 1:
                    preparation_confirmed = True
                    result = SpecialistResult(
                        agent=agent_id,
                        status=SpecialistStatus.PARTIAL,
                        answer=(
                            "O MCP confirmou a preparação do relatório. A interpretação "
                            "pelo modelo falhou; isso não confirma envio. Confirme o envio "
                            "somente após conferir a preparação."
                        ),
                        error=failure_reason,
                    )
            except Exception:
                updated_errors.append("email_preparation_recovery_unavailable")

    action_summary: str | None = None
    dispatch_evidence: EmailDispatchEvidence | None = None
    if (
        agent_id == AgentId.SECRETARY
        and email_dispatch is not None
        and state.get("send_intent_requested")
    ):
        recovery_context = recovery_context_builder(state, configurable)
        dispatch_result = await email_dispatch.recover_interaction_outcome(
            configurable=recovery_context
        )
        if dispatch_result is not None:
            try:
                dispatch_evidence = EmailDispatchEvidence.model_validate_json(dispatch_result)
                action_summary = dispatch_evidence.message
            except ValueError, TypeError:
                action_summary = (
                    "O MCP não confirmou o estado do envio; nenhum novo envio foi solicitado."
                )

    return SecretaryFlowResult(
        result=result,
        failure_reason=failure_reason,
        errors=tuple(updated_errors),
        recipient_question=recipient_question,
        email_preparation_confirmed=preparation_confirmed,
        email_action_summary=action_summary,
        email_dispatch_evidence=dispatch_evidence,
    )


def _now_utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)
