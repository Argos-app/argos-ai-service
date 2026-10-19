"""Compatibilidade de imports para o componente de observação da aplicação."""

from app.application.operation_observation import (
    OperationRecorder,
    active_collector,
    observe_operation,
)

__all__ = ["OperationRecorder", "active_collector", "observe_operation"]
