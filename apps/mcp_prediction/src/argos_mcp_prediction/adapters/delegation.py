"""Contexto delegado associado à requisição MCP atual, como no MCP de dados."""

from uuid import UUID

from mcp.server.lowlevel.server import request_ctx
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from packages.security.access import AccessContext, AuthorizedScope
from packages.security.access_policy import AccessPolicy
from packages.security.delegation import DelegationTokenVerifier
from packages.security.errors import AccessContextError

_ACCESS_CONTEXT_KEY = "argos_mcp_prediction_access_context"


def get_access_context() -> AccessContext:
    # O SDK transporta a requisição HTTP atual nos metadados de cada mensagem.
    # Um ContextVar próprio seria herdado da inicialização da sessão MCP e
    # manteria uma delegação antiga nas chamadas seguintes da mesma sessão.
    try:
        request = request_ctx.get().request
    except LookupError as exc:
        raise AccessContextError("contexto de acesso obrigatório") from exc
    if request is None:
        raise AccessContextError("contexto de acesso obrigatório")
    context = request.scope.get("state", {}).get(_ACCESS_CONTEXT_KEY)
    if not isinstance(context, AccessContext):
        raise AccessContextError("contexto de acesso obrigatório")
    return context


async def authorize_mcp_call(
    access_policy: AccessPolicy,
    *,
    operation: str,
    selected_property_ids: tuple[UUID, ...] | None = None,
) -> AuthorizedScope:
    """Obtém contexto somente do middleware e revalida-o antes da consulta."""

    return await access_policy.authorize(
        get_access_context(),
        operation=operation,
        selected_property_ids=selected_property_ids,
    )


class DelegationContextMiddleware:
    """Exige e valida contexto do usuário antes de qualquer chamada a `/mcp`."""

    def __init__(self, app: ASGIApp, verifier: DelegationTokenVerifier) -> None:
        self._app = app
        self._verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/mcp":
            await self._app(scope, receive, send)
            return
        values = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"x-argos-delegation"
        ]
        if len(values) != 1:
            await JSONResponse(
                {"detail": "contexto de acesso obrigatório ou inválido"}, status_code=401
            )(scope, receive, send)
            return
        try:
            token = values[0].decode("ascii")
            context = self._verifier.verify(token)
        except (UnicodeDecodeError, AccessContextError):
            await JSONResponse(
                {"detail": "contexto de acesso obrigatório ou inválido"}, status_code=401
            )(scope, receive, send)
            return

        # Copiar o estado evita compartilhar identidade entre requisições.
        scoped_request = {
            **scope,
            "state": {**scope.get("state", {}), _ACCESS_CONTEXT_KEY: context},
        }
        await self._app(scoped_request, receive, send)
