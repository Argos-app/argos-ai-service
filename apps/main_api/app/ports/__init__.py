"""Portas de entrada/saída do núcleo da API principal."""

from app.ports.conversation_repository import ConversationRepository
from app.ports.execution_event_repository import ExecutionEventRepository
from app.ports.interaction_repository import InteractionRepository
from app.ports.memory_generators import SessionSummaryGenerator, UserMemoryExtractor
from app.ports.session_repository import SessionRepository
from app.ports.session_summary_repository import SessionSummaryRepository
from app.ports.user_memory_repository import UserMemoryRepository

__all__ = [
    "ConversationRepository",
    "ExecutionEventRepository",
    "InteractionRepository",
    "SessionRepository",
    "SessionSummaryGenerator",
    "SessionSummaryRepository",
    "UserMemoryExtractor",
    "UserMemoryRepository",
]
