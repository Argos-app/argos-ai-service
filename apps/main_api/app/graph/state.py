from app.agent_schemas.inputs import QuestionFormattingRequest, SessionContext
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
    session_context: SessionContext | None

    pii_map: dict[str, str]
    sanitized_message: str
    recipient_selectors: dict[str, dict[str, str]]
    send_intent_requested: bool
    send_intent_source_interaction_id: str | None
    email_action_summary: str | None

    route: list[str]
    route_plan: RoutePlan | None
    ordered_tasks: tuple[RouteTask, ...]
    current_task_index: int
    called_agents: list[str]
    task_outcomes: tuple[TaskOutcome, ...]
    sources: list[SourceReference]
    report_artifact_refs: dict[str, tuple[str, ...]]
    report_artifact_origin_interaction_id: str | None
    report_source_renewal_count: int

    missing_fields: list[str]
    clarification_request: QuestionFormattingRequest | None
    clarification_question: str | None
    question_id: str | None
    clarification_expires_at: str | None
    pending_task_id: str | None
    resume_target: str | None

    draft_result: OrchestratorResult | None
    judge_result: JudgeResult | None

    final_answer: str | None
    errors: list[str]
    retry_count: int
