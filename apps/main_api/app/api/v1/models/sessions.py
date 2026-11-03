"""Contratos de criação e listagem de sessões."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.application.contracts.base import ApplicationModel


class Session(ApplicationModel):
    """Sessão de conversa e propriedades acessíveis ao usuário autenticado."""

    schema_version: Literal["1"] = Field(
        default="1", description="Versão do contrato de resposta da sessão.", examples=["1"]
    )
    session_id: UUID = Field(
        description="Identificador único da sessão.",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    farm_ids: list[UUID] = Field(
        description="Identificadores das propriedades autorizadas para esta sessão.",
        examples=[["123e4567-e89b-12d3-a456-426614174010"]],
    )
    created_at: datetime = Field(
        description="Data e hora de criação da sessão em UTC.",
        examples=["2026-10-27T12:00:00Z"],
    )
