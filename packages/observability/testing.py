"""Fakes de memória para testes do núcleo de observabilidade."""

from .models import Observation


class InMemoryObservationWriter:
    """Writer fake que aceita observações sem dependência de MongoDB."""

    def __init__(self, *, accept: bool = True, error: Exception | None = None) -> None:
        self.accept = accept
        self.error = error
        self.observations: list[Observation] = []

    def try_submit(self, observation: Observation) -> bool:
        if self.error is not None:
            raise self.error
        if not self.accept:
            return False
        self.observations.append(observation)
        return True
