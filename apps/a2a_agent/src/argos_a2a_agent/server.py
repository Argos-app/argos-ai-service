"""Fundação ASGI autenticada; especialistas e operações A2A são etapas posteriores."""

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

from argos_a2a_agent.settings import Settings


class StaticServiceTokenVerifier:
    """Mesmo padrão de Bearer interno dos MCPs, sem delegação de usuário nesta etapa."""

    def __init__(self, expected_token: str) -> None:
        if not expected_token or any(char.isspace() for char in expected_token):
            raise ValueError("token interno deve ser não vazio e sem espaços")
        self._expected_token = expected_token.encode("utf-8")

    def is_expected_token(self, token: str) -> bool:
        return hmac.compare_digest(token.encode("utf-8"), self._expected_token)


class ServiceAuthenticationMiddleware:
    def __init__(self, app: Any, verifier: StaticServiceTokenVerifier) -> None:
        self._app = app
        self._verifier = verifier

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
        await self._app(scope, receive, send)


def create_a2a_server(
    settings: Settings,
    *,
    readiness_check: Callable[[], Awaitable[Any]],
) -> Any:
    """Cria o transporte protegido, sem publicar skills ou executores provisórios.

    A montagem de Agent Card, SDK routes e tasks depende dos especialistas e da
    autorização de usuário. O Bearer aqui autentica somente o serviço chamador.
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

    return Starlette(
        routes=[Route("/health/live", live), Route("/health/ready", ready)],
        middleware=[
            Middleware(
                ServiceAuthenticationMiddleware,
                verifier=StaticServiceTokenVerifier(settings.internal_service_token),
            )
        ],
    )
