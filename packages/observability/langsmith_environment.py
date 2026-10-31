"""Funções auxiliares para resolver o ambiente do agente LangSmith pela configuração."""

_LANGSMITH_ENVIRONMENTS = {"local", "development", "staging", "production"}


def resolve_langsmith_agent_environment(
    configured_environment: str | None,
    app_environment: str | None,
) -> str:
    """Resolve o ambiente do agente LangSmith por uma substituição ou por APP_ENV."""
    configured = (configured_environment or "").strip().casefold()
    app_env = (app_environment or "").strip().casefold()
    raw_value = configured or app_env
    value = raw_value or "production"
    if value not in _LANGSMITH_ENVIRONMENTS:
        raise ValueError(
            "LangSmith Agent environment deve ser local, development, staging ou production"
        )
    return value
