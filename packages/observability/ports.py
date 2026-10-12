"""Interfaces independentes de framework e infraestrutura para observabilidade."""

from collections.abc import Callable
from datetime import datetime
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

    def try_submit(self, observation: Observation) -> bool:
        """Retorna imediatamente se aceitou a observação na fila limitada."""


class ObservationCollector(Protocol):
    """Porta que operações funcionais usam para emitir observações."""

    def emit(self, observation: Observation) -> None:
        """Emite em modo best-effort e não altera o resultado funcional."""


CollectionFailureReporter = Callable[[str], None]
