"""Contratos de criação e listagem de sessões."""

from datetime import datetime
from uuid import UUID

from app.application.contracts.base import ApplicationModel


class SessionView(ApplicationModel):
    session_id: UUID
    farm_ids: tuple[UUID, ...]
    created_at: datetime
