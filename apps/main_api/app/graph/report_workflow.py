"""Validação e persistência de fontes assinadas de relatórios."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.application.report_artifact_service import (
    ReportSourceExpiredError,
    ReportSourceValidationError,
    validate_report_source,
)
from app.domain.report_artifacts import ReportArtifactExpiredError, ReportArtifactNotFoundError
from app.graph.state import AgentState
from app.ports.report_artifact_repository import ReportArtifactRepository
from langchain_core.runnables import RunnableConfig


@dataclass(frozen=True, slots=True)
class ReportDependencyResolution:
    blocked: bool
    errors: tuple[str, ...]
    refs_by_dependency: dict[str, tuple[str, ...]]
    artifact_scopes: dict[str, dict[str, str]]
    rewind_update: dict[str, Any] | None = None


async def resolve_report_dependencies(
    state: AgentState,
    *,
    farm_dependencies: tuple[Any, ...],
    blocked: bool,
    errors: list[str],
    repository: ReportArtifactRepository | None,
    config: RunnableConfig | None,
) -> ReportDependencyResolution:
    """Valida as referências assinadas que a Secretária usará como fonte."""
    refs_by_dependency: dict[str, tuple[str, ...]] = {}
    artifact_scopes: dict[str, dict[str, str]] = {}
    updated_errors = list(errors)
    if not farm_dependencies or blocked:
        return ReportDependencyResolution(
            blocked, tuple(updated_errors), refs_by_dependency, artifact_scopes
        )

    configurable = (config or {}).get("configurable") or {}
    try:
        principal_user_id = UUID(str(configurable["argos_user_id"]))
        authorized_property_ids = tuple(
            UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
        )
        origin_interaction_id = UUID(
            str(state.get("report_artifact_origin_interaction_id") or state.get("interaction_id"))
        )
        session_id = UUID(state["session_id"])
        if principal_user_id != UUID(state["user_id"]):
            raise ValueError("principal_mismatch")
        if repository is None:
            raise ReportArtifactNotFoundError("artifact_store_unavailable")
        for dependency in farm_dependencies:
            refs = tuple(state.get("report_artifact_refs", {}).get(dependency.task.task_id, ()))
            if not refs:
                raise ReportArtifactNotFoundError("missing_report_artifact")
            for reference in refs:
                artifact = await repository.get(
                    UUID(reference),
                    principal_user_id=principal_user_id,
                    session_id=session_id,
                    interaction_id=origin_interaction_id,
                    task_id=dependency.task.task_id,
                )
                validate_report_source(
                    artifact.envelope,
                    principal_user_id=principal_user_id,
                    lot_id=artifact.envelope.report.requested_lot.lot_id,
                    authorized_property_ids=authorized_property_ids,
                )
                artifact_scopes[reference] = {
                    "interaction_id": str(origin_interaction_id),
                    "task_id": dependency.task.task_id,
                }
            refs_by_dependency[dependency.task.task_id] = refs
    except ReportArtifactExpiredError, ReportSourceExpiredError:
        if state.get("report_source_renewal_count", 0) < 1:
            source_index = next(
                index
                for index, candidate in enumerate(state["ordered_tasks"])
                if candidate.task_id == farm_dependencies[0].task.task_id
            )
            prior_outcomes = tuple(
                outcome
                for outcome in state.get("task_outcomes", ())
                if outcome.task.task_id
                in {task.task_id for task in state["ordered_tasks"][:source_index]}
            )
            prior_task_ids = {outcome.task.task_id for outcome in prior_outcomes}
            prior_sources = []
            for outcome in prior_outcomes:
                for source in outcome.result.sources:
                    if source not in prior_sources:
                        prior_sources.append(source)
            return ReportDependencyResolution(
                blocked=blocked,
                errors=tuple(updated_errors),
                refs_by_dependency=refs_by_dependency,
                artifact_scopes=artifact_scopes,
                rewind_update={
                    "current_task_index": source_index,
                    "task_outcomes": prior_outcomes,
                    "sources": prior_sources,
                    "report_artifact_refs": {
                        key: value
                        for key, value in state.get("report_artifact_refs", {}).items()
                        if key in prior_task_ids
                    },
                    "report_source_renewal_count": (
                        state.get("report_source_renewal_count", 0) + 1
                    ),
                },
            )
        blocked = True
        updated_errors.append("report_source_expired_after_renewal")
    except ReportArtifactNotFoundError, ReportSourceValidationError, ValueError:
        blocked = True
        updated_errors.append("invalid_or_missing_report_artifact")

    return ReportDependencyResolution(
        blocked, tuple(updated_errors), refs_by_dependency, artifact_scopes
    )


async def persist_farm_report_references(
    state: AgentState,
    *,
    task_id: str,
    repository: ReportArtifactRepository,
    config: RunnableConfig | None,
) -> tuple[dict[str, Any], str | None]:
    """Valida e guarda apenas referências assinadas criadas pela tarefa atual."""
    configurable = (config or {}).get("configurable") or {}
    try:
        principal_user_id = UUID(str(configurable["argos_user_id"]))
        session_id = UUID(state["session_id"])
        interaction_id = UUID(state["interaction_id"])
        artifacts = await repository.list_for_task(
            principal_user_id=principal_user_id,
            session_id=session_id,
            interaction_id=interaction_id,
            task_id=task_id,
        )
        for artifact in artifacts:
            validate_report_source(
                artifact.envelope,
                principal_user_id=principal_user_id,
                lot_id=artifact.envelope.report.requested_lot.lot_id,
                authorized_property_ids=tuple(
                    UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
                ),
                now=artifact.created_at,
            )
        refs = tuple(str(artifact.reference) for artifact in artifacts)
        refs_by_task = dict(state.get("report_artifact_refs", {}))
        if refs:
            refs_by_task[task_id] = refs
            update = {
                "report_artifact_refs": refs_by_task,
                "report_artifact_origin_interaction_id": str(interaction_id),
            }
        else:
            refs_by_task.pop(task_id, None)
            update = {"report_artifact_refs": refs_by_task}
        return update, None
    except Exception:
        return (
            {
                "report_artifact_refs": {
                    key: value
                    for key, value in state.get("report_artifact_refs", {}).items()
                    if key != task_id
                }
            },
            "invalid_report_artifact_created",
        )
