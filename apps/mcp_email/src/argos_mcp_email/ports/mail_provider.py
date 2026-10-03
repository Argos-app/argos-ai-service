"""Porta para o provedor externo de e-mail."""

from dataclasses import dataclass
from typing import Protocol

from packages.contracts import RecipientRef


@dataclass(frozen=True, slots=True)
class ProviderSendResult:
    accepted: bool
    provider_message_id: str | None = None
    error_code: str | None = None
    delivery_unknown: bool = False


class MailProvider(Protocol):
    async def send(
        self,
        *,
        sender: str,
        recipients: tuple[RecipientRef, ...],
        subject: str,
        body: str,
    ) -> ProviderSendResult:
        """Solicita o envio e diferencia falha de aceite desconhecido."""
