"""Erros sem dependência do protocolo que os adaptadores usam para responder."""

from enum import StrEnum


class ApplicationErrorCode(StrEnum):
    BAD_REQUEST = "bad_request"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID_INPUT = "invalid_input"
    UNAVAILABLE = "unavailable"


class ApplicationError(Exception):
    def __init__(self, code: ApplicationErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)
