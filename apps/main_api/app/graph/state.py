from app.agent_schemas.inputs import QuestionFormattingRequest
from app.agent_schemas.results import JudgeResult, RoutePlan, RouteTask, SourceReference
from app.agent_schemas.synthesis import OrchestratorResult, TaskOutcome
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    user_id: str
    farm_id: str
    session_id: str
    interaction_id: str | None
    input_kind: str
    answer_question_id: str | None

    pii_map: dict[str, str]
    sanitized_message: str

    route: list[str]
    route_plan: RoutePlan | None
    ordered_tasks: tuple[RouteTask, ...]
    current_task_index: int
    called_agents: list[str]
    task_outcomes: tuple[TaskOutcome, ...]
    sources: list[SourceReference]

    missing_fields: list[str]
    clarification_request: QuestionFormattingRequest | None
    clarification_question: str | None
    question_id: str | None
    pending_task_id: str | None
    resume_target: str | None

    draft_result: OrchestratorResult | None
    judge_result: JudgeResult | None

    final_answer: str | None
    errors: list[str]
    retry_count: int
