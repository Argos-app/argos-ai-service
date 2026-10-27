"""Contratos de criação e listagem de sessões."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.application.contracts.base import ApplicationModel


class Session(ApplicationModel):
    schema_version: Literal["1"] = "1"
    session_id: UUID
    farm_ids: list[UUID]
    created_at: datetime
