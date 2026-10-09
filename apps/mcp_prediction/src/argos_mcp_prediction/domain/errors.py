"""Erros de domínio seguros para tradução pelo adapter de entrada."""


class DomainError(Exception):
    code = "domain_error"


class InvalidConsumptionDataError(DomainError):
    code = "inconsistent_data"


class ConsumptionSourceUnavailableError(DomainError):
    code = "consumption_source_unavailable"


class ConsumptionResultLimitError(DomainError):
    code = "consumption_result_limit_exceeded"
