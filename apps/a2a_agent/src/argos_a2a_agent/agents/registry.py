"""Registry local das duas skills documentais do serviço A2A."""

from collections.abc import Mapping

from argos_a2a_agent.agents.protocol import DocumentAgent
from packages.contracts.a2a import (
    DocumentAnswerResponse,
    DocumentQuestionRequest,
    DocumentSkill,
)
from packages.security.access import DocumentAccessContext


class DocumentAgentRegistry:
    """Resolve documental/legal e encaminha mixed ao coordenador local."""

    def __init__(
        self,
        agents: Mapping[DocumentSkill, DocumentAgent],
        mixed_agent: DocumentAgent,
    ) -> None:
        expected = {DocumentSkill.DOCUMENTAL, DocumentSkill.LEGAL}
        if set(agents) != expected:
            raise ValueError("registry deve conter exatamente Documental e Legal")
        if any(agent.skill != skill for skill, agent in agents.items()):
            raise ValueError("a skill declarada não corresponde ao agente registrado")
        if mixed_agent.skill != DocumentSkill.MIXED:
            raise ValueError("o coordenador deve implementar a skill mixed")
        self._agents = dict(agents)
        self._mixed_agent = mixed_agent

    @property
    def skills(self) -> tuple[DocumentSkill, DocumentSkill]:
        """Skills especialistas a publicar no protocolo; mixed é composição interna."""

        return (DocumentSkill.DOCUMENTAL, DocumentSkill.LEGAL)

    def resolve(self, skill: DocumentSkill) -> DocumentAgent:
        if skill == DocumentSkill.MIXED:
            return self._mixed_agent
        try:
            return self._agents[skill]
        except KeyError as exc:
            raise ValueError("skill documental não registrada") from exc

    async def execute(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
    ) -> DocumentAnswerResponse:
        """Despacha a pergunta sem aceitar seleção de especialista fora do contrato."""

        return await self.resolve(request.skill).execute(request, context)
