"""Política de acesso com as regras de escopo do MCP de dados."""

from argos_mcp_data.domain import AuthorizedScope
from argos_mcp_data.domain.errors import AccessDeniedError
from packages.security.access_directory import AccessDirectory
from packages.security.access_policy import BaseAccessPolicy


class AccessPolicy(BaseAccessPolicy[AuthorizedScope]):
    def __init__(self, access_directory: AccessDirectory) -> None:
        super().__init__(
            access_directory,
            scope_builder=AuthorizedScope.build,
            denied_error=AccessDeniedError,
        )
