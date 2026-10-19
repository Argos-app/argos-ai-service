"""Fluxo do consumidor HTTP contra o MCP real e Mailpit; Fazenda usa fixture assinada."""

import base64
import json
import os
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from app.agent_schemas.results import (
    JudgeResult,
    RoutePlan,
    RouteTask,
    SourceReference,
    SpecialistResult,
)
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.application.email_workflow import EmailWorkflow
from app.application.report_artifact_service import validate_report_source
from app.domain.report_artifacts import ReportArtifact
from app.domain.user_access import UserAccess, UserFarm
from app.graph.workflow import build_graph
from app.ports.agent import AgentId
from app.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from packages.contracts import (
    LotReportData,
    LotReportEnvelope,
    LotReportIdentity,
)


class _UserAccessRepository:
    def __init__(self, users: tuple[UserAccess, ...]) -> None:
        self._users = {user.user_id: user for user in users}

    async def find_by_firebase_uid(self, firebase_uid: str) -> UserAccess | None:
        return next(
            (user for user in self._users.values() if user.firebase_uid == firebase_uid), None
        )

    async def find_by_user_id(self, user_id: UUID) -> UserAccess | None:
        return self._users.get(user_id)


def _signed_report(private_key, *, user_id: UUID, property_id: UUID) -> LotReportEnvelope:
    now = datetime.now(UTC)
    envelope = LotReportEnvelope(
        report=LotReportData(
            requested_lot=LotReportIdentity(
                lot_id=uuid4(),
                lot_code="BOI-FELIZ",
                lot_category="ENGORDA",
                lot_active=True,
                lot_is_exception=False,
                lot_head_count=20,
                lot_open_date=date(2026, 1, 1),
                fit_for_slaughter=True,
                herd_id=uuid4(),
                herd_name="Rebanho Feliz",
                herd_breed="Nelore",
                property_id=property_id,
                property_name="Fazenda de teste",
            ),
            queried_at=now,
        ),
        principal_user_id=user_id,
        authorized_property_ids=(property_id,),
        valid_until=now + timedelta(minutes=5),
        signature_key_id="test-key",
        signature="placeholder",
        payload_hash="placeholder",
    )
    canonical = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode()
    return envelope.model_copy(
        update={
            "payload_hash": "sha256:" + sha256(canonical).hexdigest(),
            "signature": base64.b64encode(private_key.sign(canonical)).decode(),
        }
    )


class _Agent:
    def __init__(self, function):
        self._function = function
        self.error = None
        self.result = None
        self.last_input = None

    async def run(self, input, *, context=None):
        self.last_input = input
        try:
            self.result = await self._function(input, context or {})
            return self.result
        except Exception as exc:
            self.error = repr(exc)
            raise


class _Factory:
    def __init__(self, **agents):
        self._agents = {AgentId(name): agent for name, agent in agents.items()}
        self.calls = []

    def create(self, agent_id):
        self.calls.append(agent_id.value)
        return self._agents[agent_id]


def _factory(
    *, artifacts, private_key, user_id, property_id, email_client, preparations, report_title
):
    async def router(_input, _context):
        return RoutePlan(
            intent="relatório de lote",
            tasks=(
                RouteTask(
                    task_id="farm-report",
                    agent="farm",
                    goal="Consultar dados do lote",
                    brief_sanitized="Consultar BOI-FELIZ",
                ),
                RouteTask(
                    task_id="prepare-email",
                    agent="secretary",
                    goal="Preparar e enviar o relatório",
                    brief_sanitized="Preparar relatório para o destinatário autorizado",
                    depends_on=("farm-report",),
                ),
            ),
        )

    async def farm(_input, context):
        limits = context.get("argos_execution_limits")
        assert limits is not None
        limits.record_tool_success(
            "farm", "resolve_report_lot_report_source", '{"report_source_ready": true}'
        )
        session_id = UUID(context["session_id"])
        interaction_id = UUID(context["interaction_id"])
        task_id = context["argos_task_id"]
        await artifacts.save(
            ReportArtifact(
                reference=uuid4(),
                envelope=_signed_report(private_key, user_id=user_id, property_id=property_id),
                principal_user_id=user_id,
                session_id=session_id,
                interaction_id=interaction_id,
                task_id=task_id,
                created_at=datetime.now(UTC),
            )
        )
        return SpecialistResult(
            agent="farm",
            status="success",
            answer="Relatório do lote consultado.",
            sources=(SourceReference(title="Relatório do lote", reference="farm-report"),),
        )

    async def secretary(input, context):
        preparation_workflow = EmailWorkflow(
            email_client=email_client,
            report_artifacts=artifacts,
            preparations=preparations,
        )
        dispatch_workflow = EmailDispatchWorkflow(
            email_client=email_client,
            report_artifacts=artifacts,
            preparations=preparations,
        )
        report_ref = UUID(input.dependency_results[0].report_artifact_refs[0])
        prepared = json.loads(
            await preparation_workflow.prepare(
                report_artifact_ref=report_ref,
                report_title=report_title,
                configurable=context,
            )
        )
        answer = prepared["message"]
        if prepared["status"] == "prepared" and context.get("argos_send_intent_requested"):
            dispatched = json.loads(await dispatch_workflow.send(configurable=context))
            answer = dispatched["message"]
        return SpecialistResult(agent="secretary", status="success", answer=answer)

    async def orchestrator(_input, _context):
        from app.agent_schemas.synthesis import OrchestratorResult

        return OrchestratorResult(draft_answer="Solicitação de relatório processada.")

    async def judge(_input, _context):
        return JudgeResult(decision="approved", reason="O resumo respeita o resultado da ação.")

    return _Factory(
        router=_Agent(router),
        farm=_Agent(farm),
        secretary=_Agent(secretary),
        orchestrator=_Agent(orchestrator),
        judge=_Agent(judge),
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_dispatch"),
    [
        ("Envie o relatório do lote BOI-FELIZ para Matheus Fazan.", True),
        ("Prepare o relatório do lote BOI-FELIZ para Matheus Fazan.", False),
    ],
)
async def test_public_api_prepares_sends_and_replays_without_duplicate_email(
    mcp_harness, message, expected_dispatch
):
    """Exercises API → graph → real email MCP → Mongo/Mailpit using a signed fake Farm source."""
    mongo_uri = os.getenv("MAIN_API_MONGO_TEST_URI")
    if not mongo_uri:
        pytest.skip("defina MAIN_API_MONGO_TEST_URI para executar a integração do consumidor")

    from app.main import create_app
    from motor.motor_asyncio import AsyncIOMotorClient

    harness = mcp_harness
    user_id = harness.requester
    property_id = uuid4()
    access_token_key = Ed25519PrivateKey.generate()
    access_token_private_key = base64.b64encode(
        access_token_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode()
    other_user_id = uuid4()
    users = (
        UserAccess(
            user_id=user_id,
            firebase_uid="firebase-test-user",
            name="Solicitante de teste",
            email="requester@example.test",
            active=True,
            blocked_until=None,
            permission_code="USER",
            permission_name="Usuário",
            permission_active=True,
            farms=(UserFarm(property_id, "Fazenda de teste"),),
        ),
        UserAccess(
            user_id=other_user_id,
            firebase_uid="firebase-test-other-user",
            name="Outro usuário de teste",
            email="other@example.test",
            active=True,
            blocked_until=None,
            permission_code="USER",
            permission_name="Usuário",
            permission_active=True,
            farms=(UserFarm(property_id, "Fazenda de teste"),),
        ),
    )
    database_name = "argos_main_consumer_" + uuid4().hex
    settings = Settings(
        mongo_uri=mongo_uri,
        mongo_database=database_name,
        access_token_key_id="main-test-key",
        access_token_private_key=access_token_private_key,
        mcp_email_url=harness.url,
        mcp_email_token=harness.token,
    )
    app = create_app(settings)
    token = app.state.access_token_codec.issue(user_id, "firebase-test-user")
    headers = {"Authorization": f"Bearer {token}"}
    mongo = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    try:
        with TestClient(app) as client:
            app.state.user_access_repository = _UserAccessRepository(users)
            persistence = app.state.persistence
            agent_factory = _factory(
                artifacts=persistence.report_artifacts,
                private_key=harness.private_key,
                user_id=user_id,
                property_id=property_id,
                email_client=app.state.mcp_clients.email,
                preparations=persistence.email_preparations,
                report_title=harness.title,
            )
            graph = build_graph(
                agent_factory,
                checkpointer=app.state.checkpointer,
                report_artifacts=persistence.report_artifacts,
                email_preparations=persistence.email_preparations,
                email_dispatch=EmailDispatchWorkflow(
                    email_client=app.state.mcp_clients.email,
                    preparations=persistence.email_preparations,
                    report_artifacts=persistence.report_artifacts,
                ),
            )
            app.state.agent_graph = graph

            session_response = client.post("/v1/sessions", headers=headers)
            assert session_response.status_code == 201, session_response.text
            session_id = session_response.json()["session_id"]
            client_message_id = str(uuid4())
            request_body = {
                "kind": "message",
                "client_message_id": client_message_id,
                "message": message,
            }
            other_user_token = app.state.access_token_codec.issue(
                other_user_id, "firebase-test-other-user"
            )
            other_headers = {"Authorization": f"Bearer {other_user_token}"}
            cross_user = client.post(
                f"/v1/sessions/{session_id}/interactions",
                headers=other_headers,
                json=request_body,
            )
            assert cross_user.status_code == 404

            response = client.post(
                f"/v1/sessions/{session_id}/interactions",
                headers=headers,
                json=request_body,
            )
            assert response.status_code == 200, response.text
            public_result = response.json()
            artifact_document = await mongo[database_name]["ai_report_artifacts"].find_one(
                {
                    "principal_user_id": str(user_id),
                    "session_id": session_id,
                    "interaction_id": public_result["interaction_id"],
                    "task_id": "farm-report",
                }
            )
            assert artifact_document is not None
            farm_artifact = ReportArtifact(
                reference=UUID(artifact_document["_id"]),
                envelope=LotReportEnvelope.model_validate(artifact_document["envelope"]),
                principal_user_id=user_id,
                session_id=UUID(session_id),
                interaction_id=UUID(public_result["interaction_id"]),
                task_id="farm-report",
                created_at=artifact_document["created_at"],
            )
            validate_report_source(
                farm_artifact.envelope,
                principal_user_id=user_id,
                lot_id=farm_artifact.envelope.report.requested_lot.lot_id,
                authorized_property_ids=(property_id,),
            )
            assert agent_factory._agents[AgentId.FARM].error is None
            assert agent_factory._agents[AgentId.SECRETARY].error is None
            assert agent_factory._agents[AgentId.SECRETARY].result is not None, agent_factory.calls
            secretary_answer = agent_factory._agents[AgentId.SECRETARY].result.answer
            if expected_dispatch:
                assert "O provedor aceitou o despacho" in secretary_answer, secretary_answer
                assert "não confirma a entrega" in secretary_answer, secretary_answer
            assert public_result["response"]["kind"] == "answer"
            public_text = public_result["response"]["text"]
            if expected_dispatch:
                authorization = await mongo[database_name]["ai_email_send_authorizations"].find_one(
                    {
                        "principal_user_id": str(user_id),
                        "session_id": session_id,
                        "source_interaction_id": public_result["interaction_id"],
                    }
                )
                assert authorization is not None
                operation_reference = await mongo[database_name][
                    "ai_email_operation_references"
                ].find_one({"_id": authorization["_id"]})
                assert operation_reference is not None
                assert operation_reference["last_known_status"] == "accepted"
                assert "O provedor aceitou o despacho" in public_text
                assert "não confirma a entrega" in public_text
            else:
                assert "O provedor aceitou o despacho" not in public_text
            assert "matheus@example.test" not in public_text
            assert harness.title not in public_text
            stored_report = await harness.database.prepared_reports.find_one({})
            assert stored_report is not None
            assert stored_report["body"] not in public_text

            replay = client.post(
                f"/v1/sessions/{session_id}/interactions",
                headers=headers,
                json=request_body,
            )
            assert replay.status_code == 200
            assert replay.json() == public_result
            altered_replay = client.post(
                f"/v1/sessions/{session_id}/interactions",
                headers=headers,
                json={**request_body, "message": "Envie este relatório para outro destinatário."},
            )
            assert altered_replay.status_code == 409
            expected_count = int(expected_dispatch)
            assert await harness.database.email_dispatches.count_documents({}) == expected_count
            assert await harness.database.prepared_reports.count_documents({}) == 1

            stored_action_count = await mongo[database_name][
                "ai_email_send_authorizations"
            ].count_documents({})
            stored_operation_count = await mongo[database_name][
                "ai_email_operation_references"
            ].count_documents({})
            assert stored_action_count == stored_operation_count == expected_count

        # A new API instance replays the persisted HTTP interaction without calling the graph/MCP.
        restarted_app = create_app(settings)
        with TestClient(restarted_app) as restarted_client:
            restarted_app.state.user_access_repository = _UserAccessRepository(users)
            replay_after_restart = restarted_client.post(
                f"/v1/sessions/{session_id}/interactions",
                headers=headers,
                json=request_body,
            )
            assert replay_after_restart.status_code == 200
            assert replay_after_restart.json() == public_result
        expected_count = int(expected_dispatch)
        assert await harness.database.email_dispatches.count_documents({}) == expected_count
        assert len(await harness.messages()) == expected_count
    finally:
        await mongo.drop_database(database_name)
        mongo.close()
