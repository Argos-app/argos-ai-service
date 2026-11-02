"""Autenticação de serviço no transporte MCP."""

import hmac

from starlette.responses import JSONResponse


class McpBearerMiddleware:
    """Valida a credencial antes de encaminhar requisições HTTP."""

    def __init__(self, app, token):
        if not token:
            raise ValueError("token interno de serviço é obrigatório")
        self.app = app
        self.expected = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            values = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
            parts = values[0].split(None, 1) if len(values) == 1 else []
            if (
                len(parts) != 2
                or parts[0].lower() != b"bearer"
                or not hmac.compare_digest(parts[1], self.expected)
            ):
                response = JSONResponse(
                    {"detail": "credencial de serviço inválida"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
