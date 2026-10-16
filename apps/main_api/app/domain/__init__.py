"""Modelos independentes de transporte, agentes e armazenamento."""

from app.domain.memory import (
    MemoryFieldAction,
    SessionSummary,
    SummaryFact,
    UserMemory,
    UserMemoryChange,
    UserMemoryFieldChange,
)
from app.domain.sessions import (
    ConversationMessage,
    ExecutionEvent,
    ExecutionMessage,
    ExecutionMessageRole,
    ExecutionStatus,
    ExecutionTarget,
    InteractionKind,
    InteractionRecord,
    InteractionStatus,
    MessageKind,
    MessageRole,
    ProcessingLease,
    SessionRecord,
)

__all__ = [
    "ConversationMessage",
    "ExecutionEvent",
    "ExecutionMessage",
    "ExecutionMessageRole",
    "ExecutionStatus",
    "ExecutionTarget",
    "InteractionKind",
    "InteractionRecord",
    "InteractionStatus",
    "MessageKind",
    "MessageRole",
    "MemoryFieldAction",
    "ProcessingLease",
    "SessionRecord",
    "SessionSummary",
    "SummaryFact",
    "UserMemory",
    "UserMemoryChange",
    "UserMemoryFieldChange",
]
