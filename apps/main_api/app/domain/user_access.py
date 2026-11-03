"""Dados cadastrais e fazendas carregados da fonte relacional."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UserFarm:
    """Fazenda ativa acessível por propriedade ou vínculo vigente."""

    farm_id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class UserAccess:
    """Visão de leitura do usuário e do escopo de fazendas vigente."""

    user_id: UUID
    firebase_uid: str | None
    name: str
    email: str
    active: bool
    blocked_until: datetime | None
    permission_code: str
    permission_name: str
    permission_active: bool
    farms: tuple[UserFarm, ...]
