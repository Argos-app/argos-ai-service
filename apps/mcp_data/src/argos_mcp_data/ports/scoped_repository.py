"""Contrato genérico para repositories de negócio sempre vinculados a escopo."""

from typing import Protocol, TypeVar

from argos_mcp_data.domain.access import AuthorizedScope

RequestT = TypeVar("RequestT", contravariant=True)
ResponseT = TypeVar("ResponseT", covariant=True)


class PropertyScopedRepository(Protocol[RequestT, ResponseT]):
    """Todo repository de dados por propriedade recebe escopo autorizado."""

    async def query(self, scope: AuthorizedScope, request: RequestT) -> ResponseT: ...
