"""Cria e valida os modelos usados pelo runtime de agentes do principal."""

from collections.abc import Callable, Mapping
from typing import Any

from app.agents.registry import DEFAULT_AGENT_REGISTRY, ModelPolicyId
from app.ports.agent import AgentId
from app.settings import Settings


class ModelRuntimeConfigurationError(RuntimeError):
    """O provider não oferece as capacidades exigidas pelos agentes registrados."""


MODEL_POLICY_SETTINGS = {
    ModelPolicyId.ROUTER: "model_router_name",
    ModelPolicyId.FARM: "model_farm_name",
    ModelPolicyId.DOCTOR: "model_doctor_name",
    ModelPolicyId.ANALYST: "model_analyst_name",
    ModelPolicyId.SECRETARY: "model_secretary_name",
    ModelPolicyId.INFORMATION: "model_information_name",
    ModelPolicyId.ORCHESTRATOR: "model_orchestrator_name",
    ModelPolicyId.JUDGE: "model_judge_name",
    ModelPolicyId.QUESTION_FORMATTER: "model_question_formatter_name",
}


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
    """Cria os modelos configurados por agente sem consultar o provider no bootstrap.

    O segundo item indica por que o runtime ainda não está disponível. Isso
    permite manter liveness ativo e readiness fechado enquanto falta configuração
    ou credencial, sem expor detalhes do segredo no healthcheck.
    """
    model_names = {
        policy: getattr(settings, setting_name)
        for policy, setting_name in MODEL_POLICY_SETTINGS.items()
    }
    if settings.model_mode == "simulated":
        from app.infrastructure.agents.simulated_model import SimulatedChatModel

        simulated_model = SimulatedChatModel()
        models = {policy: simulated_model for policy in ModelPolicyId}
        validate_model_capabilities(models)
        return models, None
    if all(value is None for value in model_names.values()):
        return None, "model_runtime_not_configured"
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 200
        for value in model_names.values()
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
        model_instances: dict[str, Any] = {}
        models = {}
        for policy, model_name in model_names.items():
            if model_name not in model_instances:
                model_instances[model_name] = chat_model_factory(
                    model=model_name,
                    **shared_options,
                )
            models[policy] = model_instances[model_name]
    except Exception as exc:
        raise ModelRuntimeConfigurationError(
            "não foi possível inicializar os modelos configurados"
        ) from exc

    validate_model_capabilities(models)
    return models, None
