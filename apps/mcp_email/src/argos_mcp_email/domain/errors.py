"""Erros previsíveis das regras de domínio do MCP de e-mail."""


class DomainError(Exception):
    """Erro de negócio seguro para ser traduzido pelo adapter de entrada."""

    code = "domain_error"


class ReportNotFoundError(DomainError):
    code = "report_not_found"


class ReportExpiredError(DomainError):
    code = "report_expired"


class ReportOwnershipError(DomainError):
    code = "report_ownership_mismatch"


class UnresolvedRecipientsError(DomainError):
    code = "unresolved_recipients"


class InvalidDispatchTransitionError(DomainError):
    code = "invalid_dispatch_transition"


class IdempotencyConflictError(DomainError):
    code = "idempotency_conflict"


class SourceVerificationError(DomainError):
    code = "source_verification_failed"


class RecipientDirectoryError(DomainError):
    code = "recipient_directory_failed"
