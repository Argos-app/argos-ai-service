"""Porta para revalidar acesso atual sem consultar dados de negócio."""

from typing import Protocol
from uuid import UUID


class AccessDirectory(Protocol):
    async def get_active_property_ids(self, user_id: UUID) -> set[UUID]: ...
