"""Tipos de identidade e escopo de acesso."""

from argos_mcp_data.domain.access import AccessContext, AuthorizedScope
from argos_mcp_data.domain.errors import AccessContextError, AccessDeniedError

__all__ = ["AccessContext", "AccessContextError", "AccessDeniedError", "AuthorizedScope"]
