"""Emissão best-effort que mantém falhas de telemetria fora do fluxo funcional."""

import logging

from .models import Observation
from .ports import CollectionFailureReporter, ObservationCollector, ObservationWriter
from .sanitization import sanitize_observation

_LOGGER = logging.getLogger(__name__)


class BestEffortObservationCollector(ObservationCollector):
    """Envia ao writer sem propagar falha ou registrar dados da observação."""

    def __init__(
        self,
        writer: ObservationWriter,
        *,
        report_failure: CollectionFailureReporter | None = None,
    ) -> None:
        self._writer = writer
        self._report_failure = report_failure or self._log_failure

    def emit(self, observation: Observation) -> None:
        try:
            accepted = self._writer.try_submit(sanitize_observation(observation))
        except Exception:
            self._report("writer_error")
            return
        if not accepted:
            self._report("queue_full")

    def _report(self, code: str) -> None:
        try:
            self._report_failure(code)
        except Exception:
            # O canal de diagnóstico também não pode alterar a operação observada.
            return

    @staticmethod
    def _log_failure(code: str) -> None:
        _LOGGER.warning("observability collection failed", extra={"failure_code": code})


class DisabledObservationCollector(ObservationCollector):
    """Coletor inerte para telemetria desabilitada ou configuração indisponível."""

    def emit(self, observation: Observation) -> None:
        del observation
