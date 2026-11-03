"""Porta para revalidar acesso atual sem consultar dados de negócio."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UserPropertyAccess:
    user_id: UUID
    property_ids: set[UUID]


class AccessDirectory(Protocol):
    async def get_user_property_access(self, firebase_uid: str) -> UserPropertyAccess | None: ...
