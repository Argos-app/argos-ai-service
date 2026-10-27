"""Contratos HTTP de autenticação."""

from pydantic import BaseModel, ConfigDict, Field


class AccessTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1)
