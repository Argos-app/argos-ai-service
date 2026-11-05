"""Mantém visíveis erros do transporte que não sejam o fechamento conhecido do SSE."""

import logging

from anyio import ClosedResourceError
from argos_mcp_email.bootstrap import _ClosedSseStreamFilter


def test_closed_sse_stream_is_warning_and_other_errors_are_unchanged() -> None:
    log_filter = _ClosedSseStreamFilter()
    for message, error, expected_level in (
        ("Error in standalone SSE writer", ClosedResourceError(), logging.WARNING),
        ("Error in standalone SSE writer", ValueError(), logging.ERROR),
        ("Other transport error", ClosedResourceError(), logging.ERROR),
    ):
        record = logging.LogRecord(
            "mcp.server.streamable_http",
            logging.ERROR,
            __file__,
            1,
            message,
            (),
            (type(error), error, None),
        )
        assert log_filter.filter(record)
        assert record.levelno == expected_level
        assert (record.exc_info is None) == (expected_level == logging.WARNING)
