"""Emissão best-effort que mantém falhas de telemetria fora do fluxo funcional."""

import logging
from time import monotonic

from .models import Observation
from .ports import (
    CollectionFailureReporter,
    ObservationCollector,
    ObservationWriter,
    SubmissionResult,
)
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
        self._report_failure = report_failure
        self._last_report_at: dict[str, float] = {}

    def emit(self, observation: Observation) -> None:
        try:
            result = self._writer.try_submit(sanitize_observation(observation))
        except Exception:
            self._report("writer_error")
            return
        if result != SubmissionResult.ACCEPTED:
            self._report(result.value)

    def _report(self, code: str) -> None:
        try:
            if self._report_failure is not None:
                self._report_failure(code)
            else:
                self._log_failure(code)
        except Exception:
            # O canal de diagnóstico também não pode alterar a operação observada.
            return

    def _log_failure(self, code: str) -> None:
        now = monotonic()
        last_report = self._last_report_at.get(code)
        if last_report is not None and now - last_report < 30:
            return
        self._last_report_at[code] = now
        _LOGGER.warning("observability collection failed", extra={"failure_code": code})


class DisabledObservationCollector(ObservationCollector):
    """Coletor inerte para telemetria desabilitada ou configuração indisponível."""

    def emit(self, observation: Observation) -> None:
        del observation
