"""Validações compartilhadas por valores de domínio."""

from datetime import datetime, timedelta


def require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} deve usar UTC")
