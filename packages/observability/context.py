"""Contexto de correlação e parentesco entre observações aninhadas."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

_correlation_id: ContextVar[UUID | None] = ContextVar("argos_correlation_id", default=None)
_parent_observation_id: ContextVar[UUID | None] = ContextVar(
    "argos_parent_observation_id", default=None
)
_user_id: ContextVar[UUID | None] = ContextVar("argos_user_id", default=None)


def current_correlation_id() -> UUID | None:
    return _correlation_id.get()


def current_parent_observation_id() -> UUID | None:
    return _parent_observation_id.get()


def current_user_id() -> UUID | None:
    return _user_id.get()


def correlation_key(correlation_id: UUID | str) -> str:
    """Stable non-UUID key for correlating traces after UUID anonymization."""
    value = str(correlation_id)
    try:
        value = str(UUID(value))
    except ValueError:
        pass
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def bind_correlation(correlation_id: UUID) -> Iterator[None]:
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


@contextmanager
def observation_scope(observation_id: UUID) -> Iterator[None]:
    token: Token[UUID | None] = _parent_observation_id.set(observation_id)
    try:
        yield
    finally:
        _parent_observation_id.reset(token)


@contextmanager
def bind_user_id(user_id: UUID) -> Iterator[None]:
    token: Token[UUID | None] = _user_id.set(user_id)
    try:
        yield
    finally:
        _user_id.reset(token)
