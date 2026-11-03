"""Porta técnica de leitura SQL para os casos de uso futuros."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class SqlReader(Protocol):
    async def fetch_all(self, query: str, *parameters: Any) -> Sequence[Mapping[str, Any]]: ...
