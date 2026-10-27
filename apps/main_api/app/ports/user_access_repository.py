"""Porta somente leitura para cadastro e escopo de fazendas."""

from typing import Protocol

from app.domain.user_access import UserAccess


class UserAccessRepository(Protocol):
    async def find_by_firebase_uid(self, firebase_uid: str) -> UserAccess | None: ...
