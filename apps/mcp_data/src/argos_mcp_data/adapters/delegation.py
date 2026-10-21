"""HTTP adapter for the shared Ed25519 delegation verifier."""

import time
from collections.abc import Callable
from uuid import UUID

from mcp.server.lowlevel.server import request_ctx
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from argos_mcp_data.application import AccessPolicy
from argos_mcp_data.domain import AccessContext, AccessContextError, AuthorizedScope
from packages.security.delegation import DelegationTokenVerifier as SharedDelegationTokenVerifier

_ACCESS_CONTEXT_KEY = "argos_mcp_data_access_context"
DATA_AGENT_OPERATIONS = {
    "fazenda": frozenset({"cattle.read", "medicine.read", "report.read"}),
    "doutor": frozenset({"medicine.read"}),
    "analista": frozenset({"cattle.read"}),
}


class DelegationTokenVerifier(SharedDelegationTokenVerifier):
    """Verificador compartilhado com política de agentes própria do MCP de dados."""

    def __init__(
        self,
        trusted_keys: dict[str, str],
        *,
        issuer: str,
        audience: str,
        agent_operations: dict[str, frozenset[str]] | None = None,
        max_ttl_seconds: int = 300,
        clock_skew_seconds: int = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            trusted_keys,
            issuer=issuer,
            audience=audience,
            agent_operations=(
                DATA_AGENT_OPERATIONS if agent_operations is None else agent_operations
            ),
            max_ttl_seconds=max_ttl_seconds,
            clock_skew_seconds=clock_skew_seconds,
            clock=clock,
        )


def get_access_context() -> AccessContext:
    """Lê a delegação validada pelo middleware da requisição MCP atual."""
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
    """Obtém o contexto do middleware e revalida seu escopo no banco."""
    return await access_policy.authorize(
        get_access_context(),
        operation=operation,
        selected_property_ids=selected_property_ids,
    )


class DelegationContextMiddleware:
    """Exige e valida contexto delegado antes de qualquer chamada ao MCP."""

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
            context = self._verifier.verify(values[0].decode("ascii"))
        except UnicodeDecodeError, AccessContextError:
            await JSONResponse(
                {"detail": "contexto de acesso obrigatório ou inválido"}, status_code=401
            )(scope, receive, send)
            return

        scoped_request = {
            **scope,
            "state": {**scope.get("state", {}), _ACCESS_CONTEXT_KEY: context},
        }
        await self._app(scoped_request, receive, send)
