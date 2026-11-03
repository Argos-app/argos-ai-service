"""Modelos estritos de entrada e saída dos casos de uso."""

from pydantic import BaseModel, ConfigDict


class ApplicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
