from langgraph.graph import MessagesState

from apps.main_api.app.agent_schemas.results import JudgeResult, SpecialistResult


class AgentState(MessagesState):
    user_id: str
    farm_id: str
    session_id: str

    pii_map: dict[str, str]

    route: list[str]
    called_agents: list[str]
    specialist_results: list[SpecialistResult]

    missing_fields: list[str]
    clarification_question: str | None

    judge_result: JudgeResult | None

    final_answer: str | None
    errors: list[str]
    retry_count: int
