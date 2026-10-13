"""Identidade mínima que os casos de uso precisam para aplicar autorização."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ActorContext:
    user_id: UUID
    farm_ids: tuple[UUID, ...]
