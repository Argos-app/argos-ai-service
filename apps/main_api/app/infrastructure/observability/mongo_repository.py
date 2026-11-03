"""Adapter Mongo compartilhado para observabilidade da Main API."""

from packages.observability.mongo_repository import (
    MongoObservationRepository,
    ObservationConflictError,
)

__all__ = ["MongoObservationRepository", "ObservationConflictError"]
