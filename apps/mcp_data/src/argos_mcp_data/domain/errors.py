"""Erros locais de autorização e erro compartilhado de contexto delegado."""

from packages.security.errors import AccessContextError

__all__ = ["AccessContextError", "AccessDeniedError"]


class AccessDeniedError(Exception):
    """Operação ou propriedade fora do escopo autorizado."""
