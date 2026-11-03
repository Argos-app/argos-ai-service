"""Adapter Mongo compartilhado para observabilidade do A2A."""

from packages.observability.mongo_repository import (
    MongoObservationRepository,
    ObservationConflictError,
)

__all__ = ["MongoObservationRepository", "ObservationConflictError"]
