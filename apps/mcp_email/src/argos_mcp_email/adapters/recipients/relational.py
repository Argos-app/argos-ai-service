"""Adapter de destinatários baseado na tabela relacional ``app_user``."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from argos_mcp_email.domain import RecipientDirectoryError
from packages.contracts import (
    RecipientRef,
    RecipientResolution,
    RecipientResolutionStatus,
    RecipientSelector,
)


class SqlExecutor(Protocol):
    async def fetch_all(self, query: str, *parameters: Any) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class RecipientRow:
    """Projeção mínima de ``app_user`` usada pelo MCP de e-mail."""

    id: UUID
    name: str
    email: str


class RelationalRecipientDirectory:
    """Resolve destinatários sem exigir que eles tenham acesso à fazenda."""

    _QUERY = """
        SELECT id, name, email
          FROM app_user
         WHERE active = TRUE
           AND (
                (LOWER(BTRIM(email)) = LOWER(BTRIM($1)) AND $2 = 'email')
                OR
                (LOWER(BTRIM(name)) = LOWER(BTRIM($1)) AND $2 = 'name')
           )
         ORDER BY name, email
    """

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    @staticmethod
    def _lookup_value(selector: RecipientSelector) -> str:
        if selector.kind != "name":
            return selector.value
        return selector.value.translate(
            str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})
        )

    async def resolve(
        self,
        selectors: tuple[RecipientSelector, ...],
    ) -> tuple[RecipientResolution, ...]:
        resolutions: list[RecipientResolution] = []
        for selector in selectors:
            try:
                rows = await self._executor.fetch_all(
                    self._QUERY,
                    self._lookup_value(selector),
                    selector.kind,
                )
            except Exception as exc:
                raise RecipientDirectoryError(
                    "falha ao consultar cadastro de destinatários"
                ) from exc

            matches = tuple(
                RecipientRef(
                    recipient_id=RecipientRow(**row).id,
                    name=RecipientRow(**row).name,
                    email=RecipientRow(**row).email,
                )
                for row in rows
            )
            if len(matches) == 1:
                status = RecipientResolutionStatus.RESOLVED
            elif len(matches) > 1:
                status = RecipientResolutionStatus.AMBIGUOUS
            else:
                status = RecipientResolutionStatus.NOT_FOUND
            resolutions.append(
                RecipientResolution(selector=selector, status=status, matches=matches)
            )
        return tuple(resolutions)
