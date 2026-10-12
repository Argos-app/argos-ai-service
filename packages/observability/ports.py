"""Interfaces independentes de framework e infraestrutura para observabilidade."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .models import Observation


class Clock(Protocol):
    """Relógio substituível para medir duração sem depender do relógio civil."""

    def now_utc(self) -> datetime:
        """Retorna a hora civil atual, com timezone UTC."""

    def monotonic_ns(self) -> int:
        """Retorna a leitura monotônica em nanossegundos."""


class ObservationWriter(Protocol):
    """Aceita uma observação sem esperar por rede ou persistência."""

    def try_submit(self, observation: Observation) -> SubmissionResult:
        """Retorna imediatamente o resultado da tentativa na fila limitada."""


class SubmissionResult(StrEnum):
    ACCEPTED = "accepted"
    QUEUE_FULL = "queue_full"
    DOCUMENT_TOO_LARGE = "document_too_large"
    INVALID_DOCUMENT = "invalid_document"
    NOT_RUNNING = "writer_not_running"


class ObservationCollector(Protocol):
    """Porta que operações funcionais usam para emitir observações."""

    def emit(self, observation: Observation) -> None:
        """Emite em modo best-effort e não altera o resultado funcional."""


CollectionFailureReporter = Callable[[str], None]
