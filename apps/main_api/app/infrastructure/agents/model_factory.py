"""Cria e valida os modelos usados pelo runtime de agentes do principal."""

from collections.abc import Callable, Mapping
from typing import Any

from app.agents.registry import DEFAULT_AGENT_REGISTRY, ModelPolicyId
from app.ports.agent import AgentId
from app.settings import Settings
from langchain_core.runnables import Runnable


class ModelRuntimeConfigurationError(RuntimeError):
    """O objeto LangChain não permite configurar os recursos exigidos pelos agentes."""


MODEL_POLICY_SETTINGS = {
    ModelPolicyId.FAST: "model_fast_name",
    ModelPolicyId.SPECIALIST: "model_specialist_name",
}


class _CrossProviderChatModel(Runnable[Any, Any]):
    """Combina modelos ligados na mesma chamada usando fallback do LangChain."""

    def __init__(self, primary: Any, fallback: Any, retryable_errors: tuple[type[Exception], ...]):
        self._primary = primary
        self._fallback = fallback
        self._retryable_errors = retryable_errors

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._primary.invoke(input, config=config, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return await self._primary.ainvoke(input, config=config, **kwargs)

    def get_num_tokens(self, text: str) -> int:
        """Conta tokens pelo maior dos dois providers para respeitar ambos os limites."""
        counts = []
        for model in (self._primary, self._fallback):
            counter = getattr(model, "get_num_tokens", None)
            if not callable(counter):
                continue
            count = counter(text)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ModelRuntimeConfigurationError(
                    "provider retornou uma contagem de tokens inválida"
                )
            counts.append(count)
        if not counts:
            raise ModelRuntimeConfigurationError(
                "nenhum provider configurado oferece contagem de tokens"
            )
        return max(counts)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        primary = self._primary.with_structured_output(schema, **kwargs)
        fallback = self._fallback.with_structured_output(
            schema,
            method="json_schema",
            **kwargs,
        )
        return primary.with_fallbacks([fallback], exceptions_to_handle=self._retryable_errors)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        primary = self._primary.bind_tools(tools, **kwargs)
        fallback_kwargs = dict(kwargs)
        response_format = fallback_kwargs.pop("response_format", None)
        if isinstance(response_format, Mapping):
            specification = response_format.get("json_schema", {})
            schema = specification.get("schema") if isinstance(specification, Mapping) else None
            if not isinstance(schema, Mapping):
                raise ModelRuntimeConfigurationError("LangGraph enviou um JSON Schema inválido")
            fallback_kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": dict(schema)}
            }
        fallback = self._fallback.bind_tools(tools, strict=True, **fallback_kwargs)
        return primary.with_fallbacks(
            [fallback],
            exceptions_to_handle=self._retryable_errors,
        )


def validate_model_capabilities(
    models: Mapping[ModelPolicyId, Any],
    *,
    registry=DEFAULT_AGENT_REGISTRY,
    simulated: bool = False,
) -> None:
    """Confere suporte local a saída estruturada e às ferramentas declaradas.

    A validação é local: ela não chama o provider e não confirma que o endpoint
    aceita ferramentas ou saída estruturada durante uma requisição real.
    """
    checked_models: set[int] = set()
    for agent_id in AgentId:
        definition = registry.get(agent_id)
        model = models.get(definition.model_policy)
        if model is None:
            raise ModelRuntimeConfigurationError(
                f"modelo ausente para a política {definition.model_policy.value}"
            )
        if not simulated and id(model) not in checked_models:
            structured_output = getattr(model, "with_structured_output", None)
            if not callable(structured_output):
                raise ModelRuntimeConfigurationError(
                    f"modelo de {agent_id.value} não permite saída estruturada nativa"
                )
            checked_models.add(id(model))

        if not definition.tool_slots:
            continue
        bind_tools = getattr(model, "bind_tools", None)
        if not callable(bind_tools):
            raise ModelRuntimeConfigurationError(
                f"modelo de {agent_id.value} não permite vincular ferramentas"
            )
        try:
            if bind_tools(()) is None:
                raise ValueError("LangChain não criou modelo vinculado a ferramentas")
        except Exception as exc:
            raise ModelRuntimeConfigurationError(
                f"LangChain não consegue vincular ferramentas ao modelo de {agent_id.value}"
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

    O adapter de chat é configurado para chamadas sem raciocínio adicional."""
    model_names = {
        policy: getattr(settings, setting_name)
        for policy, setting_name in MODEL_POLICY_SETTINGS.items()
    }
    if settings.model_mode == "simulated":
        from app.infrastructure.agents.simulated_model import SimulatedChatModel

        simulated_model = SimulatedChatModel()
        models = {policy: simulated_model for policy in ModelPolicyId}
        validate_model_capabilities(models, simulated=True)
        return models, None
    if all(value is None for value in model_names.values()):
        return None, "model_runtime_not_configured"
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 200
        for value in model_names.values()
    ):
        return None, "model_configuration_incomplete"
    if not settings.openai_base_url:
        return None, "model_endpoint_not_configured"
    if not settings.openai_api_key:
        return None, "model_credentials_not_configured"
    if settings.anthropic_api_key and not settings.anthropic_base_url:
        return None, "fallback_model_endpoint_not_configured"

    if chat_model_factory is None:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ModelRuntimeConfigurationError(
                "provider LangChain OpenAI não está instalado"
            ) from exc
        chat_model_factory = ChatOpenAI

    shared_options: dict[str, Any] = {
        "api_key": settings.openai_api_key,
        "base_url": settings.openai_base_url,
        "timeout": settings.model_timeout_seconds,
        "max_retries": settings.model_max_retries,
        "max_tokens": settings.model_max_output_tokens,
        "reasoning_effort": "none",
    }
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

    if settings.anthropic_api_key:
        models = _attach_anthropic_fallbacks(models, settings)
    validate_model_capabilities(models)
    return models, None


def _attach_anthropic_fallbacks(
    primary_models: Mapping[ModelPolicyId, Any], settings: Settings
) -> dict[ModelPolicyId, Any]:
    """Vincula Claude ao modelo OpenAI sem reexecutar o grafo de agente."""
    names = {
        ModelPolicyId.FAST: settings.model_fast_fallback_name,
        ModelPolicyId.SPECIALIST: settings.model_specialist_fallback_name,
    }
    if any(
        not isinstance(name, str) or not name.strip() or len(name) > 200 for name in names.values()
    ):
        raise ModelRuntimeConfigurationError("nomes dos modelos Anthropic estão inválidos")
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ModelRuntimeConfigurationError(
            "provider LangChain Anthropic não está instalado"
        ) from exc

    shared_options: dict[str, Any] = {
        "api_key": settings.anthropic_api_key,
        "base_url": settings.anthropic_base_url,
        "timeout": settings.model_timeout_seconds,
        "max_retries": settings.model_max_retries,
        "max_tokens": settings.model_max_output_tokens,
    }
    model_instances: dict[str, Any] = {}
    fallback_models: dict[ModelPolicyId, Any] = {}
    try:
        for policy, model_name in names.items():
            if model_name not in model_instances:
                model_instances[model_name] = ChatAnthropic(
                    model=model_name,
                    **shared_options,
                )
            fallback_models[policy] = model_instances[model_name]
    except Exception as exc:
        raise ModelRuntimeConfigurationError(
            "não foi possível inicializar os modelos Anthropic de fallback"
        ) from exc
    try:
        import openai
    except ImportError as exc:
        raise ModelRuntimeConfigurationError("SDK OpenAI não está instalado") from exc
    retryable_errors = (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    )
    return {
        policy: _CrossProviderChatModel(
            primary_models[policy], fallback_models[policy], retryable_errors
        )
        for policy in ModelPolicyId
    }
