"""Porta para resolução de destinatários cadastrados."""

from typing import Protocol

from packages.contracts import RecipientResolution, RecipientSelector


class RecipientDirectory(Protocol):
    async def resolve(
        self,
        selectors: tuple[RecipientSelector, ...],
    ) -> tuple[RecipientResolution, ...]:
        """Resolve nomes/e-mails e preserva ambiguidades e ausências."""
