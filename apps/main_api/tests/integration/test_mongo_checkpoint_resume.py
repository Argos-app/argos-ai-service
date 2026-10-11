"""Valida retomada após reinicializar o processo com Mongo replica set."""

import os
from typing import Any
from uuid import uuid4

import pytest
from app.agent_schemas.results import (
    JudgeResult,
    QuestionFormatterResult,
    RoutePlan,
    RouteTask,
    SpecialistResult,
)
from app.agent_schemas.synthesis import OrchestratorResult
from app.graph.workflow import build_graph, executar_fluxo, executar_resposta
from app.infrastructure.checkpoints.mongo import create_mongo_checkpointer
from app.ports.agent import AgentId
from app.settings import Settings


class FakeAgent:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def run(self, input: Any, *, context=None) -> Any:
        del input, context
        self.calls += 1
        return self.responses.pop(0)


class FakeFactory:
    def __init__(self, **agents: FakeAgent) -> None:
        self.agents = {AgentId(name): agent for name, agent in agents.items()}

    def create(self, agent_id: AgentId) -> FakeAgent:
        return self.agents[agent_id]


def _factory(router: FakeAgent, farm: FakeAgent) -> FakeFactory:
    return FakeFactory(
        router=router,
        question_formatter=FakeAgent(QuestionFormatterResult(prompt="Qual período?")),
        farm=farm,
        orchestrator=FakeAgent(OrchestratorResult(draft_answer="Consulta retomada")),
        judge=FakeAgent(JudgeResult(decision="approved", reason="Resposta baseada no resultado")),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pending_question_resumes_after_recreating_mongo_saver():
    mongo_uri = os.getenv("MAIN_API_MONGO_TEST_URI")
    if not mongo_uri:
        pytest.skip("defina MAIN_API_MONGO_TEST_URI para executar o teste de retomada Mongo")

    settings = Settings(mongo_uri=mongo_uri, mongo_database=f"argos_checkpoint_test_{uuid4().hex}")
    interaction_id = str(uuid4())
    session_id = str(uuid4())
    router = FakeAgent(RoutePlan(intent="relatório", missing_slots=("período",)))
    checkpointer_runtime = create_mongo_checkpointer(settings)
    try:
        first_graph = build_graph(
            _factory(router, FakeAgent()), checkpointer=checkpointer_runtime.saver
        )
        first = await executar_fluxo(
            first_graph,
            "Quero um relatório",
            user_id=str(uuid4()),
            farm_id=str(uuid4()),
            session_id=session_id,
            interaction_id=interaction_id,
        )
        assert first["clarification_question"] == "Qual período?"
    finally:
        checkpointer_runtime.close()

    checkpointer_runtime = create_mongo_checkpointer(settings)
    farm = FakeAgent(SpecialistResult(agent="farm", status="success", answer="Dados de janeiro"))
    try:
        task = RouteTask(
            task_id="t1",
            agent="farm",
            goal="Consultar dados do período",
            brief_sanitized="Consultar dados do período informado",
        )
        resumed_graph = build_graph(
            _factory(FakeAgent(RoutePlan(intent="relatório", tasks=(task,))), farm),
            checkpointer=checkpointer_runtime.saver,
        )
        result = await executar_resposta(
            resumed_graph,
            "janeiro",
            question_id=str(first["question_id"]),
            session_id=session_id,
            interaction_id=str(uuid4()),
            checkpoint_namespace=interaction_id,
        )
    finally:
        checkpointer_runtime.close()

    assert result["final_answer"] == "Consulta retomada"
    assert farm.calls == 1
