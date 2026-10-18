"""Medição local com relógio monotônico e horários civis UTC."""

from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic_ns

from .ports import Clock


def _utc_millisecond(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("relógio civil deve retornar datetime com timezone")
    utc_value = value.astimezone(UTC)
    return utc_value.replace(microsecond=(utc_value.microsecond // 1000) * 1000)


class SystemClock:
    """Relógio de produção baseado em UTC e time.monotonic_ns."""

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return monotonic_ns()


@dataclass(frozen=True, slots=True)
class TimingResult:
    started_at: datetime
    ended_at: datetime
    duration_ms: float


class OperationTimer:
    """Cronometra uma única operação; chamadas repetidas a finish são idempotentes."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._started_at = _utc_millisecond(self._clock.now_utc())
        self._started_ns = self._clock.monotonic_ns()
        self._result: TimingResult | None = None

    @property
    def started_at(self) -> datetime:
        return self._started_at

    def finish(self) -> TimingResult:
        """Finaliza a medição sem derivar duração da diferença entre datetimes."""
        if self._result is not None:
            return self._result

        observed_end = _utc_millisecond(self._clock.now_utc())
        # Ajuste de relógio civil não deve produzir uma observação inválida.
        ended_at = max(self._started_at, observed_end)
        elapsed_ns = max(0, self._clock.monotonic_ns() - self._started_ns)
        self._result = TimingResult(
            started_at=self._started_at,
            ended_at=ended_at,
            duration_ms=elapsed_ns / 1_000_000,
        )
        return self._result
