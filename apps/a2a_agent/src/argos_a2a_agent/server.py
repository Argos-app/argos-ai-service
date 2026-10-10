"""Servidor ASGI autenticado e endpoint A2A do agente Informações."""

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

from argos_a2a_agent.adapters.inbound.delegation import ACCESS_CONTEXT_KEY
from argos_a2a_agent.settings import Settings
from packages.security.delegation import GlobalDocumentDelegationTokenVerifier
from packages.security.errors import AccessContextError


class StaticServiceTokenVerifier:
    """Mesmo padrão de Bearer interno dos MCPs, sem delegação de usuário nesta etapa."""

    def __init__(self, expected_token: str) -> None:
        if not expected_token or any(char.isspace() for char in expected_token):
            raise ValueError("token interno deve ser não vazio e sem espaços")
        self._expected_token = expected_token.encode("utf-8")

    def is_expected_token(self, token: str) -> bool:
        return hmac.compare_digest(token.encode("utf-8"), self._expected_token)


class ServiceAuthenticationMiddleware:
    def __init__(
        self,
        app: Any,
        verifier: StaticServiceTokenVerifier,
        delegation_verifier: GlobalDocumentDelegationTokenVerifier,
    ) -> None:
        self._app = app
        self._verifier = verifier
        self._delegation_verifier = delegation_verifier

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] == "/health/live":
            await self._app(scope, receive, send)
            return
        headers = [
            value for name, value in scope.get("headers", ()) if name.lower() == b"authorization"
        ]
        authorized = False
        if len(headers) == 1:
            scheme, _, token = headers[0].decode("latin-1").partition(" ")
            authorized = scheme.lower() == "bearer" and self._verifier.is_expected_token(token)
        if not authorized:
            from starlette.responses import JSONResponse

            response = JSONResponse(
                {"error": "credencial de serviço inválida"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        # A prontidão é chamada interna de infraestrutura. Rotas de negócio
        # exigem também o contexto assinado do usuário em cada requisição.
        if scope.get("path") != "/health/ready":
            delegation_values = [
                value
                for name, value in scope.get("headers", ())
                if name.lower() == b"x-argos-delegation"
            ]
            try:
                if len(delegation_values) != 1:
                    raise AccessContextError("contexto delegado obrigatório")
                delegated_token = delegation_values[0].decode("ascii")
                context = self._delegation_verifier.verify(delegated_token)
            except UnicodeDecodeError, AccessContextError:
                from starlette.responses import JSONResponse

                response = JSONResponse(
                    {"error": "contexto de acesso obrigatório ou inválido"}, status_code=401
                )
                await response(scope, receive, send)
                return
            scope = {
                **scope,
                "state": {**scope.get("state", {}), ACCESS_CONTEXT_KEY: context},
            }
        await self._app(scope, receive, send)


def create_a2a_server(
    settings: Settings,
    *,
    readiness_check: Callable[[], Awaitable[Any]],
    delegation_verifier: GlobalDocumentDelegationTokenVerifier,
    agent_registry: Any | None = None,
) -> Any:
    """Cria healthchecks e, quando composto, o transporte A2A protegido.

    O transporte não publica um Agent Card sem um registry funcional. Isso
    permite que etapas posteriores componham o provedor de respostas e a
    sincronização sem anunciar uma capacidade indisponível.
    """

    settings.validate_runtime()
    try:
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.responses import JSONResponse
        from starlette.routing import Route
    except ImportError as exc:
        raise RuntimeError(
            "instale as dependências do serviço A2A para iniciar o servidor"
        ) from exc

    async def live(request: Any) -> Any:
        return JSONResponse({"status": "ok"})

    async def ready(request: Any) -> Any:
        try:
            await readiness_check()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    routes = [Route("/health/live", live), Route("/health/ready", ready)]
    if agent_registry is not None:
        if not settings.a2a_endpoint_url:
            raise ValueError("A2A_ENDPOINT_URL é obrigatória para publicar o Agent Card")
        try:
            from a2a.server.request_handlers import DefaultRequestHandler
            from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
            from a2a.server.tasks import InMemoryTaskStore

            from argos_a2a_agent.adapters.inbound.a2a_server import (
                DocumentA2AExecutor,
                DocumentCallContextBuilder,
                create_agent_card,
                task_owner,
            )
        except ImportError as exc:
            raise RuntimeError("instale a2a-sdk[http-server] para publicar o endpoint A2A") from exc

        card = create_agent_card(settings.a2a_endpoint_url)
        handler = DefaultRequestHandler(
            agent_executor=DocumentA2AExecutor(agent_registry),
            task_store=InMemoryTaskStore(owner_resolver=task_owner),
            agent_card=card,
        )
        routes.extend(create_agent_card_routes(card))
        routes.extend(
            create_jsonrpc_routes(
                handler,
                rpc_url="/a2a",
                context_builder=DocumentCallContextBuilder(),
            )
        )

    return Starlette(
        routes=routes,
        middleware=[
            Middleware(
                ServiceAuthenticationMiddleware,
                verifier=StaticServiceTokenVerifier(settings.internal_service_token),
                delegation_verifier=delegation_verifier,
            )
        ],
    )
