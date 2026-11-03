"""Adapters de persistência do serviço principal."""

from app.infrastructure.persistence.mongo import MongoPersistence

__all__ = ["MongoPersistence"]
