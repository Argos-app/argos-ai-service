"""Cliente para delegação documental por A2A."""

from app.infrastructure.a2a.client import DocumentA2ACallError, DocumentA2AClient

__all__ = ["DocumentA2AClient", "DocumentA2ACallError"]
