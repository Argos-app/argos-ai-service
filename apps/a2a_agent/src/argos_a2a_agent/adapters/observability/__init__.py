"""Composição local de captura e persistência para o serviço A2A."""

from argos_a2a_agent.adapters.observability.mongo_repository import (
    MongoObservationRepository,
    ObservationConflictError,
)
from argos_a2a_agent.adapters.observability.recording import (
    OperationRecorder,
    observe_adapter,
    observe_operation,
)
from argos_a2a_agent.adapters.observability.writer import MongoObservationWriter

__all__ = [
    "MongoObservationRepository",
    "MongoObservationWriter",
    "ObservationConflictError",
    "OperationRecorder",
    "observe_adapter",
    "observe_operation",
]
