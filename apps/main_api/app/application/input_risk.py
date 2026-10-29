"""Aplica fallback de avaliação sem permitir entrada quando ambos falham."""

from app.ports.input_risk import InputRiskDecision, InputRiskEvaluator


class FallbackInputRiskEvaluator:
    def __init__(self, primary: InputRiskEvaluator, fallback: InputRiskEvaluator) -> None:
        self._evaluators = (primary, fallback)

    async def evaluate(self, text: str) -> InputRiskDecision:
        for evaluator in self._evaluators:
            try:
                decision = await evaluator.evaluate(text)
            except Exception:
                continue
            if decision in (InputRiskDecision.ALLOW, InputRiskDecision.BLOCK):
                return InputRiskDecision(decision)
        return InputRiskDecision.UNAVAILABLE
