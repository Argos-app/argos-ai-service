"""Acesso ao contexto assinado da requisição atual do A2A."""

from typing import Any

from packages.security.access import DocumentAccessContext
from packages.security.errors import AccessContextError

ACCESS_CONTEXT_KEY = "argos_a2a_document_access"


def get_document_access_context(scope: dict[str, Any]) -> DocumentAccessContext:
    """Obtém identidade exclusivamente do estado instalado pelo middleware."""

    context = scope.get("state", {}).get(ACCESS_CONTEXT_KEY)
    if not isinstance(context, DocumentAccessContext):
        raise AccessContextError("contexto documental obrigatório")
    return context
