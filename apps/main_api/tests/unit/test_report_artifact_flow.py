"""Passagem de referências de artefato do agente Fazenda ao Secretário."""

import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from app.agent_schemas.results import RouteTask, SpecialistResult
from app.agent_schemas.synthesis import TaskOutcome
from app.agents.tools.farm_reports import build_farm_report_tool
from app.domain.report_artifacts import (
    ReportArtifact,
    ReportArtifactExpiredError,
    ReportArtifactNotFoundError,
)
from app.graph.workflow import no_especialista
from app.infrastructure.agents.execution_limits import ExecutionLimits, execution_limits_scope
from app.ports.agent import AgentId
from app.ports.data_client import ReportLotCandidate, ReportLotResolution

from packages.contracts import LotReportData, LotReportEnvelope, LotReportIdentity


class _FakeAgent:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def run(self, input, *, context=None):
        self.calls.append((input, context))
        return self.response


class _FakeFactory:
    def __init__(self, **agents):
        self.agents = agents

    def create(self, agent_id):
        return self.agents[agent_id.value]


class _FakeArtifactRepository:
    def __init__(self, artifacts=(), *, expired_references=()):
        self.artifacts = tuple(artifacts)
        self.expired_references = set(expired_references)
        self.saved = []

    async def save(self, artifact):
        self.saved.append(artifact)

    async def list_for_task(self, **scope):
        return tuple(
            artifact
            for artifact in self.artifacts
            if artifact.principal_user_id == scope["principal_user_id"]
            and artifact.session_id == scope["session_id"]
            and artifact.interaction_id == scope["interaction_id"]
            and artifact.task_id == scope["task_id"]
        )

    async def get(self, reference, **scope):
        if reference in self.expired_references:
            raise ReportArtifactExpiredError()
        for artifact in self.artifacts:
            if artifact.reference != reference:
                continue
            if (
                artifact.principal_user_id == scope["principal_user_id"]
                and artifact.session_id == scope["session_id"]
                and artifact.interaction_id == scope["interaction_id"]
                and artifact.task_id == scope["task_id"]
            ):
                return artifact
        raise ReportArtifactNotFoundError("not found")


def _envelope(user_id: UUID, property_id: UUID, lot_id: UUID) -> LotReportEnvelope:
    report = LotReportData(
        requested_lot=LotReportIdentity(
            lot_id=lot_id,
            lot_code="L-1",
            lot_category="ENGORDA",
            lot_active=True,
            lot_is_exception=False,
            lot_head_count=2,
            lot_open_date=date(2026, 1, 1),
            fit_for_slaughter=False,
            herd_id=uuid4(),
            herd_name="Rebanho",
            herd_breed="Nelore",
            property_id=property_id,
            property_name="Fazenda",
        ),
        queried_at=datetime.now(UTC),
    )
    source = LotReportEnvelope(
        report=report,
        principal_user_id=user_id,
        authorized_property_ids=(property_id,),
        valid_until=datetime.now(UTC) + timedelta(minutes=15),
        payload_hash="pending",
        signature="test-signature",
        signature_key_id="test-key",
    )
    canonical = source.model_dump_json(exclude={"payload_hash", "signature"}).encode()
    return source.model_copy(update={"payload_hash": f"sha256:{sha256(canonical).hexdigest()}"})


def _state():
    user_id, property_id, session_id, interaction_id = (uuid4() for _ in range(4))
    lot_id = uuid4()
    farm = RouteTask(
        task_id="farm-task",
        agent="farm",
        goal="Obter relatório de lote",
        brief_sanitized="Consultar o lote L-1",
    )
    secretary = RouteTask(
        task_id="secretary-task",
        agent="secretary",
        goal="Preparar o relatório",
        brief_sanitized="Usar a origem validada",
        depends_on=("farm-task",),
    )
    state = {
        "user_id": str(user_id),
        "farm_id": str(property_id),
        "session_id": str(session_id),
        "interaction_id": str(interaction_id),
        "report_artifact_origin_interaction_id": str(interaction_id),
        "report_artifact_refs": {},
        "report_source_renewal_count": 0,
        "ordered_tasks": (farm, secretary),
        "current_task_index": 0,
        "task_outcomes": (),
        "sources": [],
        "called_agents": [],
        "errors": [],
    }
    config = {
        "configurable": {
            "argos_user_id": str(user_id),
            "argos_authorized_property_ids": (str(property_id),),
            "argos_request_id": str(uuid4()),
        }
    }
    return state, config, farm, secretary, user_id, property_id, session_id, interaction_id, lot_id


@pytest.mark.asyncio
async def test_farm_tool_stores_envelope_and_returns_only_its_opaque_reference():
    (
        _,
        config,
        farm_task,
        _,
        user_id,
        property_id,
        session_id,
        interaction_id,
        lot_id,
    ) = _state()
    envelope = _envelope(user_id, property_id, lot_id)

    class FakeDataClient:
        async def resolve_report_lot(self, **kwargs):
            return ReportLotResolution(
                candidates=(
                    ReportLotCandidate(
                        lot_id=lot_id,
                        lot_code="L-1",
                        property_id=property_id,
                        property_name="Fazenda",
                        herd_id=envelope.report.requested_lot.herd_id,
                        herd_name="Rebanho",
                    ),
                )
            )

        async def get_lot_report_source(self, **kwargs):
            return envelope

    repository = _FakeArtifactRepository()
    tool = build_farm_report_tool(FakeDataClient(), repository, signature_checker=lambda *_: True)
    config["configurable"].update(
        {
            "session_id": str(session_id),
            "interaction_id": str(interaction_id),
            "argos_task_id": farm_task.task_id,
        }
    )

    result = json.loads(await tool.ainvoke({"lot_code": "L-1"}, config=config))

    assert result["status"] == "report_source_ready"
    assert len(repository.saved) == 1
    assert result["report_artifact_ref"] == str(repository.saved[0].reference)
    assert envelope.payload_hash not in json.dumps(result)


@pytest.mark.asyncio
async def test_secretary_receives_only_validated_artifact_reference():
    (
        state,
        config,
        farm_task,
        secretary_task,
        user_id,
        property_id,
        session_id,
        interaction_id,
        lot_id,
    ) = _state()
    reference = uuid4()
    envelope = _envelope(user_id, property_id, lot_id)
    artifact = ReportArtifact(
        reference=reference,
        envelope=envelope,
        principal_user_id=user_id,
        session_id=session_id,
        interaction_id=interaction_id,
        task_id=farm_task.task_id,
        created_at=datetime.now(UTC),
    )
    repository = _FakeArtifactRepository((artifact,))
    farm_result = SpecialistResult(
        agent="farm",
        status="partial",
        answer="Origem consultada.",
    )
    limits = ExecutionLimits(4, 4)
    limits.record_tool_success("farm", "get_lot_report_source", '{"report_source_ready": true}')
    with execution_limits_scope(limits):
        farm_update = await no_especialista(
            state,
            factory=_FakeFactory(farm=_FakeAgent(farm_result)),
            agent_id=AgentId.FARM,
            report_artifacts=repository,
            config=config,
        )
    resumed_state = {**state, **farm_update}
    resumed_state["current_task_index"] = 1
    secretary_agent = _FakeAgent(
        SpecialistResult(agent="secretary", status="success", answer="Preparação aguardando.")
    )

    await no_especialista(
        resumed_state,
        factory=_FakeFactory(secretary=secretary_agent),
        agent_id=AgentId.SECRETARY,
        report_artifacts=repository,
        config=config,
    )

    dependency = secretary_agent.calls[0][0].dependency_results[0]
    assert dependency.report_artifact_refs == (str(reference),)
    assert envelope.payload_hash not in secretary_agent.calls[0][0].model_dump_json()
    assert dependency.answer == "Origem consultada."


@pytest.mark.asyncio
async def test_secretary_is_blocked_when_farm_dependency_has_no_artifact():
    state, config, farm_task, _, *_ = _state()
    state["current_task_index"] = 1
    state["task_outcomes"] = (
        TaskOutcome(
            task=farm_task,
            result=SpecialistResult(agent="farm", status="partial", answer="Sem envelope."),
        ),
    )
    secretary_agent = _FakeAgent(
        SpecialistResult(agent="secretary", status="success", answer="Não deveria executar.")
    )
    secretary_task = state["ordered_tasks"][1]

    update = await no_especialista(
        state,
        factory=_FakeFactory(secretary=secretary_agent),
        agent_id=AgentId.SECRETARY,
        report_artifacts=_FakeArtifactRepository(),
        config=config,
    )

    assert not secretary_agent.calls
    assert update["task_outcomes"][-1].result.status == "unavailable"
    assert "fonte assinada exigida" in update["task_outcomes"][-1].result.answer
    assert update["task_outcomes"][-1].result.error == "required_signed_report_source_missing"
    assert "invalid_or_missing_report_artifact" in update["errors"]
    assert update["task_outcomes"][-1].task == secretary_task


@pytest.mark.asyncio
async def test_secretary_blocks_artifact_bound_to_another_session():
    (
        state,
        config,
        farm_task,
        _,
        user_id,
        property_id,
        _session_id,
        interaction_id,
        lot_id,
    ) = _state()
    reference = uuid4()
    foreign_artifact = ReportArtifact(
        reference=reference,
        envelope=_envelope(user_id, property_id, lot_id),
        principal_user_id=user_id,
        session_id=uuid4(),
        interaction_id=interaction_id,
        task_id=farm_task.task_id,
        created_at=datetime.now(UTC),
    )
    state.update(
        {
            "current_task_index": 1,
            "report_artifact_refs": {farm_task.task_id: (str(reference),)},
            "task_outcomes": (
                TaskOutcome(
                    task=farm_task,
                    result=SpecialistResult(agent="farm", status="success", answer="Relatório."),
                ),
            ),
        }
    )
    secretary_agent = _FakeAgent(
        SpecialistResult(agent="secretary", status="success", answer="Não deveria executar.")
    )

    update = await no_especialista(
        state,
        factory=_FakeFactory(secretary=secretary_agent),
        agent_id=AgentId.SECRETARY,
        report_artifacts=_FakeArtifactRepository((foreign_artifact,)),
        config=config,
    )

    assert not secretary_agent.calls
    assert update["task_outcomes"][-1].result.status == "unavailable"
    assert "invalid_or_missing_report_artifact" in update["errors"]


@pytest.mark.asyncio
async def test_expired_report_source_routes_back_to_farm_once():
    (
        state,
        config,
        farm_task,
        _,
        user_id,
        property_id,
        session_id,
        interaction_id,
        lot_id,
    ) = _state()
    reference = uuid4()
    artifact = ReportArtifact(
        reference=reference,
        envelope=_envelope(user_id, property_id, lot_id),
        principal_user_id=user_id,
        session_id=session_id,
        interaction_id=interaction_id,
        task_id=farm_task.task_id,
        created_at=datetime.now(UTC),
    )
    state.update(
        {
            "current_task_index": 1,
            "report_artifact_refs": {farm_task.task_id: (str(reference),)},
            "task_outcomes": (
                TaskOutcome(
                    task=farm_task,
                    result=SpecialistResult(agent="farm", status="partial", answer="Consultado."),
                ),
            ),
        }
    )

    update = await no_especialista(
        state,
        factory=_FakeFactory(
            secretary=_FakeAgent(
                SpecialistResult(
                    agent="secretary", status="success", answer="Não deveria executar."
                )
            )
        ),
        agent_id=AgentId.SECRETARY,
        report_artifacts=_FakeArtifactRepository(
            (artifact,),
            expired_references=(reference,),
        ),
        config=config,
    )

    assert update["current_task_index"] == 0
    assert update["task_outcomes"] == ()
    assert update["report_artifact_refs"] == {}
    assert update["report_source_renewal_count"] == 1
    assert "expired_report_source_renewed_by_farm" not in update.get("errors", ())
