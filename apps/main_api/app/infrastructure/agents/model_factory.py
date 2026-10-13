"""Cria e valida os modelos usados pelo runtime de agentes do principal."""

from collections.abc import Callable, Mapping
from typing import Any

from app.agents.registry import DEFAULT_AGENT_REGISTRY, ModelPolicyId
from app.ports.agent import AgentId
from app.settings import Settings


class ModelRuntimeConfigurationError(RuntimeError):
    """O provider não oferece as capacidades exigidas pelos agentes registrados."""


FAST_POLICIES = frozenset(
    {
        ModelPolicyId.ROUTER,
        ModelPolicyId.ORCHESTRATOR,
        ModelPolicyId.JUDGE,
        ModelPolicyId.QUESTION_FORMATTER,
    }
)


def validate_model_capabilities(
    models: Mapping[ModelPolicyId, Any],
    *,
    registry=DEFAULT_AGENT_REGISTRY,
) -> None:
    """Garante suporte a saída estruturada e ferramentas antes de servir tráfego."""
    for agent_id in AgentId:
        definition = registry.get(agent_id)
        model = models.get(definition.model_policy)
        if model is None:
            raise ModelRuntimeConfigurationError(
                f"modelo ausente para a política {definition.model_policy.value}"
            )
        structured_output = getattr(model, "with_structured_output", None)
        if not callable(structured_output):
            raise ModelRuntimeConfigurationError(
                f"modelo de {agent_id.value} não oferece saída estruturada"
            )
        try:
            if structured_output(definition.output_schema) is None:
                raise ValueError("provider não criou runnable estruturado")
        except Exception as exc:
            raise ModelRuntimeConfigurationError(
                f"modelo de {agent_id.value} não aceita o schema registrado"
            ) from exc
        if definition.tool_slots:
            bind_tools = getattr(model, "bind_tools", None)
            if not callable(bind_tools):
                raise ModelRuntimeConfigurationError(
                    f"modelo de {agent_id.value} não oferece chamada de ferramentas"
                )
            try:
                if bind_tools(()) is None:
                    raise ValueError("provider não criou modelo com ferramentas")
            except Exception as exc:
                raise ModelRuntimeConfigurationError(
                    f"modelo de {agent_id.value} não aceita ferramentas"
                ) from exc


def create_configured_models(
    settings: Settings,
    *,
    chat_model_factory: Callable[..., Any] | None = None,
) -> tuple[dict[ModelPolicyId, Any] | None, str | None]:
    """Cria os modelos rápido/especialista sem consultar o provider no bootstrap.

    O segundo item indica por que o runtime ainda não está disponível. Isso
    permite manter liveness ativo e readiness fechado enquanto falta configuração
    ou credencial, sem expor detalhes do segredo no healthcheck.
    """
    model_names = (settings.model_fast_name, settings.model_specialist_name)
    if settings.model_mode == "simulated":
        from app.infrastructure.agents.simulated_model import SimulatedChatModel

        fast_model = SimulatedChatModel()
        specialist_model = SimulatedChatModel()
        models = {
            policy: fast_model if policy in FAST_POLICIES else specialist_model
            for policy in ModelPolicyId
        }
        validate_model_capabilities(models)
        return models, None
    if all(value is None for value in model_names):
        return None, "model_runtime_not_configured"
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 200 for value in model_names
    ):
        return None, "model_configuration_incomplete"
    if not settings.model_api_key:
        return None, "model_credentials_not_configured"

    if chat_model_factory is None:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ModelRuntimeConfigurationError(
                "provider LangChain OpenAI não está instalado"
            ) from exc
        chat_model_factory = ChatOpenAI

    shared_options: dict[str, Any] = {
        "api_key": settings.model_api_key,
        "timeout": settings.model_timeout_seconds,
        "max_retries": 0,
        "max_tokens": settings.model_max_output_tokens,
    }
    if settings.model_api_base_url is not None:
        shared_options["base_url"] = settings.model_api_base_url

    try:
        fast_model = chat_model_factory(model=settings.model_fast_name, **shared_options)
        specialist_model = chat_model_factory(
            model=settings.model_specialist_name,
            **shared_options,
        )
    except Exception as exc:
        raise ModelRuntimeConfigurationError(
            "não foi possível inicializar os modelos configurados"
        ) from exc

    models = {
        policy: fast_model if policy in FAST_POLICIES else specialist_model
        for policy in ModelPolicyId
    }
    validate_model_capabilities(models)
    return models, None
