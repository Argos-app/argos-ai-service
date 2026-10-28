"""Contratos HTTP de autenticação."""

from pydantic import BaseModel, ConfigDict, Field


class AccessTokenResponse(BaseModel):
    """Token emitido para autenticar chamadas à API do Argos."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(
        min_length=1,
        description="Token Bearer emitido pelo serviço para as próximas requisições.",
        examples=["eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9..."],
    )
